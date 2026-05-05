# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import re

from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.llm_agent import Agent
from google.adk.models.llm_request import LlmRequest
from google.genai import types

# === OPTIONAL: Code execution — uncomment block (1) imports, (2) helpers, and
# (3) the `code_executor=` line on root_agent below to enable Python sandbox
# execution. See README "Optional: Code Execution" for setup steps. ===
#
# import base64
# import json
# import mimetypes
#
# from google.adk.agents.invocation_context import InvocationContext
# from google.adk.code_executors.agent_engine_sandbox_code_executor import (
#     AgentEngineSandboxCodeExecutor,
# )
# from google.adk.code_executors.code_execution_utils import (
#     CodeExecutionInput,
#     CodeExecutionResult,
#     File,
# )
# from google.adk.code_executors.code_executor_context import CodeExecutorContext

os.environ['GOOGLE_CLOUD_LOCATION'] = os.getenv('MODEL_LOCATION', 'global')

# Gemini Enterprise forwards user attachments to custom Agent Engine agents as
# filename-only text markers and stashes the bytes in the ArtifactService.
# Resolve those markers back to inline_data Parts so the model sees the file.
_FILE_MARKER_RE = re.compile(
    r'<start_of_user_uploaded_file:\s*(?P<name>[^>]+?)\s*>'
)

# Mime types Claude can read directly via inline_data (image/* and PDF). Other
# types are either silently dropped or routed to the sandbox if code execution
# is enabled below.
_CLAUDE_INLINE_MIMES = ('image/', 'application/pdf')


def _is_claude_inline(mime: str | None) -> bool:
  if not mime:
    return False
  return any(mime.startswith(prefix) for prefix in _CLAUDE_INLINE_MIMES)


# === OPTIONAL: Code execution helpers — uncomment to enable. ===
#
# _SANDBOX_ONLY_MIMES = (
#     'text/csv',  # We route CSV ourselves — see optimize_data_file note below.
#     'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
#     'application/vnd.ms-excel',
#     'application/json',
#     'application/x-parquet',
#     'application/octet-stream',
#     'text/tab-separated-values',
# )
# _EXT_BY_MIME = {
#     'text/csv': '.csv',
#     'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
#     'application/vnd.ms-excel': '.xls',
#     'application/json': '.json',
#     'application/x-parquet': '.parquet',
#     'application/octet-stream': '.bin',
#     'text/tab-separated-values': '.tsv',
# }
#
# def _is_sandbox_only(mime):
#   return bool(mime) and mime in _SANDBOX_ONLY_MIMES
#
# def _generate_file_name(blob):
#   ext = _EXT_BY_MIME.get(blob.mime_type or '', '.bin')
#   return f'data_{abs(hash(blob.data)) % 10_000:04d}{ext}'
#
# def _push_to_sandbox(callback_context, file_name, inline_data):
#   ctx = CodeExecutorContext(
#       callback_context._invocation_context.session.state
#   )
#   if file_name in {f.name for f in ctx.get_input_files()}:
#     return
#   ctx.add_input_files([
#       File(
#           name=file_name,
#           content=base64.b64encode(inline_data.data).decode(),
#           mime_type=inline_data.mime_type,
#       )
#   ])
#
# class _PatchedSandboxExecutor(AgentEngineSandboxCodeExecutor):
#   """Subclass that fixes two bugs in ADK's input-file passthrough.
#
#   1. ADK passes the file payload under key 'contents' (plural) but the
#      vertexai SDK reads 'content' (singular) — files were silently dropped.
#   2. ADK base64-encodes the bytes and the SDK forwards them as-is to
#      Chunk.data, so the sandbox writes the base64 *text* instead of bytes.
#   """
#
#   def execute_code(self, invocation_context, code_execution_input):
#     sandbox_name = self._ensure_sandbox(invocation_context)
#     input_data = {'code': code_execution_input.code}
#     if code_execution_input.input_files:
#       input_data['files'] = [
#           {
#               'name': f.name,
#               'content': base64.b64decode(f.content),
#               'mimeType': f.mime_type,
#           }
#           for f in code_execution_input.input_files
#       ]
#     response = self._get_api_client().agent_engines.sandboxes.execute_code(
#         name=sandbox_name, input_data=input_data,
#     )
#     saved_files, stdout, stderr = [], '', ''
#     for output in response.outputs:
#       if output.mime_type == 'application/json' and (
#           output.metadata is None
#           or output.metadata.attributes is None
#           or 'file_name' not in output.metadata.attributes
#       ):
#         payload = json.loads(output.data.decode('utf-8'))
#         stdout = payload.get('msg_out', '')
#         stderr = payload.get('msg_err', '')
#       else:
#         file_name = ''
#         if output.metadata is not None and output.metadata.attributes is not None:
#           file_name = output.metadata.attributes.get('file_name', b'').decode('utf-8')
#         mime_type = output.mime_type or mimetypes.guess_type(file_name)[0]
#         saved_files.append(File(name=file_name, content=output.data, mime_type=mime_type))
#     return CodeExecutionResult(stdout=stdout, stderr=stderr, output_files=saved_files)
#
#   def _ensure_sandbox(self, invocation_context):
#     if self.sandbox_resource_name:
#       return self.sandbox_resource_name
#     from google.api_core import exceptions
#     from vertexai import types as vtypes
#     sandbox_name = invocation_context.session.state.get('sandbox_name')
#     if sandbox_name:
#       try:
#         sandbox = self._get_api_client().agent_engines.sandboxes.get(name=sandbox_name)
#         if sandbox and sandbox.state == 'STATE_RUNNING':
#           return sandbox_name
#       except exceptions.NotFound:
#         pass
#     operation = self._get_api_client().agent_engines.sandboxes.create(
#         spec={'code_execution_environment': {}},
#         name=self.agent_engine_resource_name,
#         config=vtypes.CreateAgentEngineSandboxConfig(
#             display_name='default_sandbox', ttl='31536000s',
#         ),
#     )
#     sandbox_name = operation.response.name
#     invocation_context.session.state['sandbox_name'] = sandbox_name
#     return sandbox_name


async def _inject_uploaded_artifacts(
    callback_context: CallbackContext, llm_request: LlmRequest
):
  """Resolve GE filename markers to inline_data Parts (and route data files
  to the sandbox if code execution is enabled below).
  """
  if not llm_request.contents:
    return None
  artifact_keys = set(await callback_context.list_artifacts())

  for content in llm_request.contents:
    if getattr(content, 'role', None) != 'user' or not content.parts:
      continue

    # Resolve GE filename markers into inline_data Parts loaded from artifacts.
    injected: set[str] = set()
    name_by_part_id: dict[int, str] = {}
    for part in list(content.parts):
      if not part.text:
        continue
      for match in _FILE_MARKER_RE.finditer(part.text):
        name = match.group('name').strip()
        if name in injected or name not in artifact_keys:
          continue
        artifact_part = await callback_context.load_artifact(name)
        if artifact_part is None or artifact_part.inline_data is None:
          continue
        new_part = types.Part(
            inline_data=types.Blob(
                mime_type=artifact_part.inline_data.mime_type,
                data=artifact_part.inline_data.data,
            )
        )
        content.parts.append(new_part)
        name_by_part_id[id(new_part)] = name
        injected.add(name)

    # === OPTIONAL: Code execution routing — uncomment to enable. ===
    # Routes binary data files (xlsx, parquet, etc.) the model can't read
    # directly into the sandbox's input-files context, replacing the inline
    # bytes with a text marker so the model knows the filename.
    #
    # rebuilt = []
    # for part in content.parts:
    #   if part.inline_data and _is_sandbox_only(part.inline_data.mime_type):
    #     name = name_by_part_id.get(id(part)) or _generate_file_name(part.inline_data)
    #     _push_to_sandbox(callback_context, name, part.inline_data)
    #     rebuilt.append(types.Part(text=f'\nAvailable file: `{name}`\n'))
    #   else:
    #     rebuilt.append(part)
    # content.parts[:] = rebuilt

  return None


_INSTRUCTION = (
    'Answer user questions to the best of your knowledge. '
    'When the user attaches images or PDFs, analyze them directly and '
    'reference their contents in your answer.'
)

# === OPTIONAL: Code execution instruction — uncomment when enabling. ===
# _INSTRUCTION += (
#     '\n\nYou also have a stateful Python sandbox. When the user asks for'
#     ' analysis of a data file (csv, xlsx, json, parquet, tsv), write Python'
#     ' in fenced ```python blocks. The file appears in the prompt as'
#     ' `Available file: NAME`; load it from the working directory'
#     ' (`pd.read_csv`, `pd.read_excel`, etc.). Pandas, numpy, matplotlib,'
#     ' seaborn, openpyxl, scikit-learn are preinstalled. Print results — bare'
#     ' expressions are not echoed. Variables persist across blocks within a'
#     ' session, so do not reload files you already loaded.'
# )

root_agent = Agent(
    model=os.getenv('MODEL_NAME', 'claude-sonnet-4-6'),
    name='root_agent',
    description='A helpful assistant for user questions.',
    instruction=_INSTRUCTION,
    before_model_callback=_inject_uploaded_artifacts,
    # === OPTIONAL: Code execution — uncomment to enable. ===
    # code_executor=_PatchedSandboxExecutor(
    #     sandbox_resource_name=os.getenv('SANDBOX_RESOURCE_NAME') or None,
    #     agent_engine_resource_name=(
    #         os.getenv('AGENT_ENGINE_RESOURCE_NAME') or None
    #     ),
    #     # Keep False: optimize_data_file=True makes ADK emit a synthetic
    #     # "Processing input file:" event before Claude responds, which Gemini
    #     # Enterprise renders as the final answer and drops the real one. We
    #     # route data files into the sandbox ourselves via the callback.
    #     optimize_data_file=False,
    # ),
)
