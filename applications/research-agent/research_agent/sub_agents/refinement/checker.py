import logging
from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from ...state_keys import StateKeys

logger = logging.getLogger(__name__)

class EscalationChecker(BaseAgent):
    """Checks research evaluation and escalates to stop the loop if grade is 'pass'."""

    def __init__(self, name: str = "escalation_checker"):
        super().__init__(name=name)

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        # 1. Check Pass/Fail Grade
        evaluation_result = ctx.session.state.get(StateKeys.RESEARCH_EVALUATION)
        grade = "fail"
        critique = "No evaluation found"
        
        if evaluation_result:
            if isinstance(evaluation_result, dict):
                grade = evaluation_result.get("grade", "fail")
                critique = evaluation_result.get("critique", "")
            elif hasattr(evaluation_result, "grade"):
                grade = evaluation_result.grade
                critique = getattr(evaluation_result, "critique", "")

        # 2. Check Iteration Limits
        current_count = ctx.session.state.get(StateKeys.REFINEMENT_LOOP_COUNT, 0)
        max_iterations = ctx.session.state.get(StateKeys.RESEARCH_PLAN_REFINEMENT_ITERATIONS, 2)
        
        # Increment counter
        current_count += 1
        ctx.session.state[StateKeys.REFINEMENT_LOOP_COUNT] = current_count
        
        # --- THINKING OUT LOUD ---
        print("\n" + "-"*50)
        print(f"🕵️  EVALUATION (Iteration {current_count}/{max_iterations})")
        print(f"   Grade: {grade.upper()}")
        if grade != "pass":
             print(f"   Critique: {critique[:200]}..." if len(critique) > 200 else f"   Critique: {critique}")
        print("-"*50 + "\n")
        # -------------------------

        logger.info(f"[{self.name}] Iteration {current_count}/{max_iterations}. Grade: {grade}")

        # 3. Decide to Continue or Stop
        should_stop = False
        stop_reason = ""
        
        if grade == "pass":
            should_stop = True
            stop_reason = "Research passed evaluation."
        elif current_count >= max_iterations:
            should_stop = True
            stop_reason = f"Max iterations ({max_iterations}) reached."

        if should_stop:
            logger.info(f"[{self.name}] Stopping loop: {stop_reason}")
            print(f"🛑 Stopping Refinement: {stop_reason}")
            yield Event(author=self.name, actions=EventActions(escalate=True))
        else:
            logger.info(f"[{self.name}] Continuing loop (Grade: {grade}, Iteration: {current_count})")
            yield Event(author=self.name)
