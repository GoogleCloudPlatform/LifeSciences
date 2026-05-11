# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
import math

from typing import Any

from google.cloud import aiplatform

from .metadata_loader import TaskMetadataLoader

logger = logging.getLogger(name=__name__)
logger.setLevel(logging.DEBUG)


class ExecuteTaskTool:
    """TxGemma Task Execution Tool.

    Executes machine learning prediction tasks using TxGemma models deployed on Vertex AI.
    Handles task metadata loading, prompt generation, model inference, and result projection.
    """

    def __init__(
        self, task_metadata_loader: TaskMetadataLoader, txgemma_predict_endpoint: str, custom_container: bool = False
    ):
        """Initialize the ExecuteTaskTool.

        Args:
            task_metadata_loader: Loader for task metadata including prompts and variables
            txgemma_predict_endpoint: Vertex AI endpoint resource name for TxGemma model
        """
        self.task_metadata_loader = task_metadata_loader
        self.txgemma_predict_endpoint = txgemma_predict_endpoint
        self.use_custom_container_contract = custom_container
        logger.debug(f"ExecuteTaskTool initialized with endpoint: {txgemma_predict_endpoint}")

    def _project_task_value(self, task_id: str, value: str) -> str:
        """Projects scaled prediction values back to their original domain space.

        Some tasks use scaled values (e.g., 0-1000) during training/prediction.
        This method transforms predictions back to their original units.

        Args:
            task_id: The task identifier
            value: The predicted value from the model

        Returns:
            The projected value in original units, or the original value if no projection needed
        """
        logger.debug(f"Projecting task value for task_id={task_id}, value={value}")

        # TODO: this approach isn't extensible and should be improved.
        def vdss(value: str) -> str:
            y = int(value)
            y_range = 1000
            x_min = 0.01
            x_range = 700 - x_min
            projected = (y / y_range) * x_range + x_min
            logger.debug(f"VDss projection: {y} -> {projected} L/kg")
            return (
                str(projected)
                + " L/kg. (this value has been projected back to the VDss space between 0.01 L/kg to 700 L/kg)"
            )

        projected_tasks = {"VDss_Lombardo": vdss}

        try:
            if task_id in projected_tasks:
                projected_value = projected_tasks[task_id](value)
                logger.debug(f"Task {task_id} projected from {value} to {projected_value}")
                return projected_value
            else:
                logger.debug(f"Task {task_id} does not require projection, returning original value")
                return value
        except Exception as e:
            logger.error(f"Error projecting task value for {task_id}: {e}", exc_info=True)
            return value  # Return original value on error

    def execute_task(self, task_id: str, parameters: str) -> dict[str, Any]:
        """Executes a TxGemma prediction task.

        Loads task metadata, generates a prompt from the template and parameters,
        calls the TxGemma model endpoint, and returns the prediction result.

        Args:
            task_id (str): Unique identifier for the task (e.g., "VDss_Lombardo", "hERG").
            parameters (dict): json dictionary of the task parameters and their values. (e.g. {"Drug SMILES": "CC"})

        Returns:
            dict: A dictionary containing the execution results with the following keys:
                * task_id (str): The task identifier.
                * prompt (str): The generated prompt sent to the model.
                * prediction (Any): The model's prediction (projected if applicable).
                * error (str, optional): Error message if execution failed.
        """
        logger.info(f"[Execute task] Starting execution for task_id={task_id}")
        logger.debug(f"[Execute task] Parameters: {parameters}")
        try:
            task_metadata = self.task_metadata_loader.get_task_metadata(task_id)
            logger.debug(f"Task metadata loaded for task_id={task_id}")
        except Exception as e:
            error_msg = f"Failed to load task metadata for '{task_id}': {e}"
            logger.exception(error_msg)
            return {"task_id": task_id, "error": error_msg}

        if task_metadata is None:
            error_msg = f"Task '{task_id}' not found in metadata."
            logger.error(f"[Execute task] {error_msg}")
            return {"task_id": task_id, "error": error_msg}

        # The agent keeps adding `_` between words for variables and with extra quotes
        # when it gets the variable name wrong. It would be good to simplify the
        # variables within the task prompts.
        logger.debug("[Execute task] Normalizing parameter keys")

        try:
            parsed_params: dict[str, Any] = json.loads(parameters)
            fixed_parameters = {}
            for param_key, param_value in parsed_params.items():
                normalized_key = param_key.replace("_", "").replace('"', "").replace("'", "")
                fixed_parameters[normalized_key] = param_value
                if normalized_key != param_key:
                    logger.debug(f"Normalized parameter key: '{param_key}' -> '{normalized_key}'")
        except json.JSONDecodeError as e:
            error_msg = f"parameters {e} are malformed. Please check the strucuture of the variables."
            logger.exception(f"[Execute task] {error_msg}")
            return {"task_id": task_id, "error": error_msg}

        logger.debug(f"[Execute task] Fixed parameters: {fixed_parameters}")

        # Get the prompt template
        prompt_template = task_metadata.prompt
        logger.debug(f"[Execute task] Prompt template length: {len(prompt_template)} characters")

        # Check for missing required variables
        missing_vars = [f"'{var}'" for var in task_metadata.required_variables if var not in fixed_parameters]

        if missing_vars:
            error_msg = (
                f"Missing required variables: {', '.join(missing_vars)}. "
                "The variable names must exactly match including spaces without extra quotes. "
                "Please provide values for these variables to execute the task."
            )
            logger.error(f"[Execute task] {error_msg}")
            logger.debug(f"[Execute task] Required variables: {task_metadata.required_variables}")
            logger.debug(f"[Execute task] Provided variables: {list(fixed_parameters.keys())}")
            return {"task_id": task_id, "error": error_msg}

        # Apply parameters to generate the prompt
        try:
            prompt = prompt_template.format(**fixed_parameters)
            logger.debug(f"[Execute task] Generated prompt (length={len(prompt)}): {prompt[:200]}...")
        except KeyError as e:
            error_msg = (
                f"Variable name {e} is malformed or not found in the prompt template. Please check the variable names."
            )
            logger.exception(f"[Execute task] {error_msg}")
            return {"task_id": task_id, "error": error_msg}
        except Exception as e:
            error_msg = f"Failed to format prompt template: {e}"
            logger.exception(f"[Execute task] {error_msg}")
            return {"task_id": task_id, "error": error_msg}

        # Initialize endpoint
        try:
            logger.debug(f"[Execute task] Initializing Vertex AI endpoint: {self.txgemma_predict_endpoint}")
            endpoint = aiplatform.Endpoint(self.txgemma_predict_endpoint)
            logger.debug("[Execute task] Endpoint initialized successfully")
        except Exception as e:
            error_msg = f"Failed to initialize Vertex AI endpoint: {e}"
            logger.exception(f"[Execute task] {error_msg}")
            return {"task_id": task_id, "error": error_msg}

        if self.use_custom_container_contract:
            # The custom container has a different contract
            instances = [
                {
                    "prompt": prompt,
                    "parameters": {
                        "details": True,  # Include token-level details
                    },
                    "max_tokens": 8,  # Expecting a short answer like "(A)" or "(B)"
                    "temperature": 0.0,  # Deterministic output for classification
                    "top_k": 1,
                    "top_p": 1.0,
                }
            ]
        else:
            instances = [
                {
                    "prompt": prompt,
                    "max_tokens": 8,  # Expecting a short answer like "(A)" or "(B)"
                    "temperature": 0.0,  # Deterministic output for classification
                    "top_k": 1,
                    "top_p": 1.0,
                }
            ]
        logger.debug(f"[Execute task] Prediction instances: {instances}")

        # Call the endpoint with error handling
        try:
            logger.info("[Execute task] Calling Vertex AI endpoint for prediction")
            response = endpoint.predict(instances=instances)
            logger.debug(f"[Execute task] Raw response received: {response}")

            predictions = response.predictions
            logger.debug(f"[Execute task] Predictions: {predictions}")

            if not predictions or len(predictions) == 0:
                error_msg = "No predictions returned from the model"
                logger.error(f"[Execute task] {error_msg}")
                return {"task_id": task_id, "prompt": prompt, "error": error_msg}

            if self.use_custom_container_contract:
                prediction = predictions[0]
                prediction_text = prediction.get("generated_text", "")
                tokens = prediction.get("details", {}).get("tokens", [])
                token_probabalities = [
                    f"char '{token.get('text')}' = {(math.exp(token.get('logprob')) * 100):.0f}%"
                    for token in tokens
                    if not token.get("special", False)
                ]
                token_probabilities_text = ", ".join(token_probabalities)
            else:
                prediction_text = predictions[0].strip()
                token_probabilities_text = ""
            logger.info(f"[Execute task] Raw prediction: {prediction_text}")

        except Exception as e:
            error_msg = f"Failed to get prediction from endpoint: {e}"
            logger.exception(f"[Execute task] {error_msg}")
            return {"task_id": task_id, "prompt": prompt, "error": error_msg}

        # Project the task value with error handling
        try:
            prediction_value = self._project_task_value(task_id, prediction_text)
            logger.info(f"[Execute task] Final prediction value: {prediction_value}")
        except Exception as e:
            error_msg = f"Failed to project task value: {e}"
            logger.exception(f"[Execute task] {error_msg}")
            # Return unprojected value on projection error
            prediction_value = prediction_text
            logger.warning(f"[Execute task] Using unprojected value due to error: {prediction_value}")

        task_result = {
            "task_id": task_id,
            "prompt": prompt,
            "prediction": prediction_value,
            "token_probabilities": token_probabilities_text,
        }

        logger.info(f"[Execute task] Task execution completed successfully: {task_result}")

        return task_result

    # def execute_task2(self, task_id: str, parameters: dict[str, Any]) -> dict[str, Any]:
    #     """Test method that returns canned responses without calling the actual model.

    #     This is a test/mock version of execute_task that returns a fixed response
    #     regardless of inputs. Useful for testing and development without making
    #     actual API calls to the Vertex AI endpoint.

    #     Args:
    #         task_id (str): Unique identifier for the task (ignored in this test method).
    #         parameters (dict): Dictionary of parameter values (ignored in this test method).

    #     Returns:
    #         dict: A dictionary containing test results with the following keys:
    #             * task_id (str): The task identifier (echoed back).
    #             * prompt (str): A test prompt string.
    #             * prediction (str): A canned test prediction response.
    #     """
    #     logger.info(f"[Execute task2] Test method called for task_id={task_id}")
    #     logger.debug(f"[Execute task2] Parameters (ignored): {parameters}")

    #     test_result = {
    #         "task_id": task_id,
    #         "prompt": "This is a test prompt generated by execute_task2",
    #         "prediction": "this is just a test method, make up whatever answer you want",
    #     }

    #     logger.info(f"[Execute task2] Returning test result: {test_result}")

    #     return test_result
