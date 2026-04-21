#!/usr/bin/env python3
"""
Script to ingest clinical data documents to Discovery Engine Data Store
with Gemini Enterprise LayoutParser (first pass), and then process complex figures/tables
using Gemini 3.1 Pro (second pass). Finally, the data store is updated with the
fully extracted text files (third pass).
"""

import os
import glob
import json
import time
from dotenv import load_dotenv

from google.cloud import storage
from google.cloud import discoveryengine_v1alpha as discoveryengine
from google.api_core.client_options import ClientOptions
from google import genai
from google.genai import types

def get_env_vars():
    env_path = os.path.join(os.path.dirname(__file__), "..", "sentinel", ".env")
    load_dotenv(env_path, override=True)
    
    return {
        "PROJECT_ID": os.getenv("PROJECT_ID"),
        "DATA_STORE_ID": os.getenv("DATA_STORE_ID"),
        "SOURCE_GCS_BUCKET": os.getenv("SOURCE_GCS_BUCKET"),
        "GEMINI_MODEL_NAME": os.getenv("GEMINI_MODEL_NAME"),
        "REGION": os.getenv("REGION", "us-central1"),
        "DISCOVERY_ENGINE_LOCATION": "us" 
    }

def upload_docs_to_gcs(project_id, bucket_name, local_dir, gcs_folder):
    print(f"=== Uploading documents from {local_dir} to GCS {gcs_folder} ===")
    storage_client = storage.Client(project=project_id)
    bucket = storage_client.bucket(bucket_name)
    
    gcs_uris = []
    
    for filepath in glob.glob(os.path.join(local_dir, "*")):
        if os.path.isfile(filepath):
            blob_name = f"{gcs_folder}/{os.path.basename(filepath)}"
            blob = bucket.blob(blob_name)
            blob.upload_from_filename(filepath)
            gcs_uri = f"gs://{bucket_name}/{blob_name}"
            print(f"Uploaded {filepath} to {gcs_uri}")
            gcs_uris.append(gcs_uri)
            
    print("Upload complete.\n")
    return f"gs://{bucket_name}/{gcs_folder}/*"

def import_documents_to_datastore(project_id, location, datastore_id, gcs_uri_pattern):
    print(f"=== Phase 1: Ingesting documents to Datastore ({datastore_id}) ===")
    print("Using data_schema='content' to ensure unstructured docs are parsed by the layout parser.")
    
    client_options = ClientOptions(api_endpoint="us-discoveryengine.googleapis.com")
    client = discoveryengine.DocumentServiceClient(client_options=client_options)
    
    parent = f"projects/{project_id}/locations/{location}/collections/default_collection/dataStores/{datastore_id}/branches/0"
    
    request = discoveryengine.ImportDocumentsRequest(
        parent=parent,
        gcs_source=discoveryengine.GcsSource(
            input_uris=[gcs_uri_pattern],
            data_schema="content" # Required for PDFs/unstructured files
        ),
        reconciliation_mode=discoveryengine.ImportDocumentsRequest.ReconciliationMode.INCREMENTAL,
    )
    
    print(f"Triggering ImportDocuments job for {gcs_uri_pattern} into {datastore_id}...")
    operation = client.import_documents(request=request)
    print("Waiting for import operation to complete (this may take a while)...")
    
    try:
        response = operation.result()
        if response.error_samples:
            print("Import finished with some errors:", response.error_samples)
        else:
            print("Import completed successfully.\n")
        return response
    except Exception as e:
        print(f"Error during import: {e}\n")
        raise e

def process_complex_entities(project_id, location, datastore_id, region, gemini_model_name):
    print("=== Phase 2: Extracting parsed text and processing complex sections with Gemini ===")
    
    client_options = ClientOptions(api_endpoint="us-discoveryengine.googleapis.com")
    client = discoveryengine.DocumentServiceClient(client_options=client_options)
    parent = f"projects/{project_id}/locations/{location}/collections/default_collection/dataStores/{datastore_id}/branches/0"

    gemini_location = "global" if "3.1-pro-preview" in gemini_model_name else region
    client_genai = genai.Client(vertexai=True, project=project_id, location=gemini_location)
    
    docs = list(client.list_documents(parent=parent))
    if not docs:
        print("No documents found in the Datastore. Exiting.")
        return []
        
    extracted_docs_dir = os.path.join(os.path.dirname(__file__), "..", "docs_extracted")
    os.makedirs(extracted_docs_dir, exist_ok=True)
    
    for doc in docs:
        # Ignore already extracted text files we might have uploaded
        if doc.content and doc.content.mime_type == "text/plain" and doc.content.uri.endswith("_extracted.txt"):
            continue

        print(f"\nProcessing document: {doc.id}")
        
        req = discoveryengine.GetProcessedDocumentRequest(
            name=doc.name,
            processed_document_type=discoveryengine.GetProcessedDocumentRequest.ProcessedDocumentType.PARSED_DOCUMENT
        )
        try:
            pdoc = client.get_processed_document(request=req)
        except Exception as e:
            print(f"  Could not get parsed document. (Make sure chunking is enabled in Datastore). Error: {e}")
            continue
            
        parsed_json = json.loads(pdoc.json_data)
        document_layout = parsed_json.get("documentLayout", {})
        blocks = document_layout.get("blocks", [])
        
        if not blocks:
            print("  No blocks found in parsed document.")
            continue
            
        text_with_placeholders = ""
        placeholder_count = 0
        
        for b in blocks:
            if "textBlock" in b:
                text_with_placeholders += b["textBlock"].get("text", "") + "\n"
            elif "tableBlock" in b:
                text_with_placeholders += f"\n[PLACEHOLDER: TABLE_{b.get('blockId', placeholder_count)}]\n"
                placeholder_count += 1
            elif "imageBlock" in b:
                text_with_placeholders += f"\n[PLACEHOLDER: IMAGE_{b.get('blockId', placeholder_count)}]\n"
                placeholder_count += 1
            else:
                text_with_placeholders += f"\n[PLACEHOLDER: FIGURE_{b.get('blockId', placeholder_count)}]\n"
                placeholder_count += 1
                
        if placeholder_count == 0:
            print("  No complex tables/figures found. Skipping Gemini extraction.")
            final_text = text_with_placeholders
        else:
            print(f"  Found {placeholder_count} placeholders. Calling Gemini '{gemini_model_name}' to extract details...")
            
            native_uri = doc.content.uri if doc.content else None
            if not native_uri:
                print("  Native URI not found. Skipping Gemini extraction.")
                final_text = text_with_placeholders
            else:
                mime_type = doc.content.mime_type
                prompt = f"""
We have extracted text from a document using a layout parser. However, complex figures, tables, and images were replaced with placeholders like [PLACEHOLDER: TABLE_x] or [PLACEHOLDER: FIGURE_y].
Please analyze the original document and replace these placeholders with the fully extracted textual and tabular data.
Return the complete, updated text with all placeholders filled in with the extracted information.

Here is the parsed text with placeholders:
{text_with_placeholders}
"""
                try:
                    document_part = types.Part.from_uri(file_uri=native_uri, mime_type=mime_type)
                    response = client_genai.models.generate_content(
                        model=gemini_model_name,
                        contents=[document_part, prompt]
                    )
                    final_text = response.text
                    print("  Successfully processed with Gemini.")
                except Exception as e:
                    print(f"  Gemini extraction failed: {e}")
                    final_text = text_with_placeholders
                    
        out_filepath = os.path.join(extracted_docs_dir, f"{doc.id}_extracted.txt")
        with open(out_filepath, "w", encoding="utf-8") as f:
            f.write(final_text)
            
        print(f"  Saved fully extracted text to {out_filepath}")
        
    return extracted_docs_dir

def main():
    env = get_env_vars()
    
    docs_dir = os.path.join(os.path.dirname(__file__), "..", "..", "docs")
    gcs_uri_pattern = upload_docs_to_gcs(env["PROJECT_ID"], env["SOURCE_GCS_BUCKET"], docs_dir, "docs")
    
    import_documents_to_datastore(
        project_id=env["PROJECT_ID"],
        location=env["DISCOVERY_ENGINE_LOCATION"],
        datastore_id=env["DATA_STORE_ID"],
        gcs_uri_pattern=gcs_uri_pattern
    )
    
    # Needs a small delay to ensure documents are processed before getting parsed documents
    # In a real pipeline, you would poll or wait for the process to complete entirely.
    print("Waiting 10 seconds for layout parser processing to finalize...")
    time.sleep(10)
    
    extracted_docs_dir = process_complex_entities(
        project_id=env["PROJECT_ID"],
        location=env["DISCOVERY_ENGINE_LOCATION"],
        datastore_id=env["DATA_STORE_ID"],
        region=env["REGION"],
        gemini_model_name=env["GEMINI_MODEL_NAME"]
    )
    
    if extracted_docs_dir and os.path.exists(extracted_docs_dir) and os.listdir(extracted_docs_dir):
        print("\n=== Phase 3: Updating Datastore with fully extracted text files ===")
        extracted_gcs_pattern = upload_docs_to_gcs(
            project_id=env["PROJECT_ID"], 
            bucket_name=env["SOURCE_GCS_BUCKET"], 
            local_dir=extracted_docs_dir, 
            gcs_folder="docs_extracted"
        )
        import_documents_to_datastore(
            project_id=env["PROJECT_ID"],
            location=env["DISCOVERY_ENGINE_LOCATION"],
            datastore_id=env["DATA_STORE_ID"],
            gcs_uri_pattern=extracted_gcs_pattern
        )
        print("Workflow complete. Datastore updated with final extracted documents.")
    else:
        print("Workflow complete. No text files were generated to update the Datastore.")

if __name__ == "__main__":
    main()
