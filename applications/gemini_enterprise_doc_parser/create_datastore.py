import os
import time
from dotenv import load_dotenv
from google.cloud import discoveryengine_v1 as discoveryengine
from google.api_core.client_options import ClientOptions

def get_env_vars():
    env_path = os.path.join(os.path.dirname(__file__), "..", "sentinel", ".env")
    load_dotenv(env_path)
    return {
        "PROJECT_ID": os.getenv("PROJECT_ID"),
        "LOCATION": "us"
    }

def create_data_store():
    env = get_env_vars()
    project_id = env["PROJECT_ID"]
    location = env["LOCATION"]
    
    # 1. Create Datastore
    ds_client = discoveryengine.DataStoreServiceClient(client_options=ClientOptions(api_endpoint="us-discoveryengine.googleapis.com"))
    
    ds_id = f"clinical-ds-{int(time.time())}"
    
    data_store = discoveryengine.DataStore(
        display_name="Clinical Data Store with LayoutParser",
        industry_vertical=discoveryengine.IndustryVertical.GENERIC,
        solution_types=[discoveryengine.SolutionType.SOLUTION_TYPE_SEARCH],
        content_config=discoveryengine.DataStore.ContentConfig.CONTENT_REQUIRED,
        document_processing_config=discoveryengine.DocumentProcessingConfig(
            default_parsing_config=discoveryengine.DocumentProcessingConfig.ParsingConfig(
                layout_parsing_config=discoveryengine.DocumentProcessingConfig.ParsingConfig.LayoutParsingConfig()
            ),
            chunking_config=discoveryengine.DocumentProcessingConfig.ChunkingConfig(
                layout_based_chunking_config=discoveryengine.DocumentProcessingConfig.ChunkingConfig.LayoutBasedChunkingConfig(
                    chunk_size=500,
                    include_ancestor_headings=True
                )
            )
        )
    )
    
    ds_request = discoveryengine.CreateDataStoreRequest(
        parent=f"projects/{project_id}/locations/{location}/collections/default_collection",
        data_store=data_store,
        data_store_id=ds_id
    )
    
    ds_operation = ds_client.create_data_store(request=ds_request)
    ds_operation.result() 

    # 2. Create Engine and Link Datastore
    engine_client = discoveryengine.EngineServiceClient(client_options=ClientOptions(api_endpoint="us-discoveryengine.googleapis.com"))
    engine_id = f"clinical-app-{int(time.time())}"
    
    # Ensure it's created as an ENTERPRISE engine to support extractive answers/segments
    engine = discoveryengine.Engine(
        display_name="Clinical Search App",
        solution_type=discoveryengine.SolutionType.SOLUTION_TYPE_SEARCH,
        industry_vertical=discoveryengine.IndustryVertical.GENERIC,
        data_store_ids=[ds_id],
        search_engine_config=discoveryengine.Engine.SearchEngineConfig(
            search_tier=discoveryengine.SearchTier.SEARCH_TIER_ENTERPRISE,
            search_add_ons=[discoveryengine.SearchAddOn.SEARCH_ADD_ON_LLM]
        )
    )
    
    eng_request = discoveryengine.CreateEngineRequest(
        parent=f"projects/{project_id}/locations/{location}/collections/default_collection",
        engine=engine,
        engine_id=engine_id
    )
    
    eng_operation = engine_client.create_engine(request=eng_request)
    eng_operation.result()
    
    # Just print the ID so bash can capture it
    print(ds_id)

if __name__ == "__main__":
    create_data_store()
