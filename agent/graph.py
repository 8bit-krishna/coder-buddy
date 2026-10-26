
import inspect
import json
import os
import re
import time
from pathlib import Path
from typing import Any, TypedDict

from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, START, END

from agent.prompts import (
    planner_prompt,
    coder_system_prompt,
)
from agent.states import Plan, TaskPlan
from agent.tools import (
    read_file,
    safe_path_for_project,
    init_project_root,
)


# ==================================================
# CONFIGURATION
# ==================================================

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")

api_key = os.getenv("GROQ_API_KEY")

if not api_key:
    raise RuntimeError(
        "GROQ_API_KEY is missing. Check the .env file in your project folder."
    )

llm = ChatGroq(
    model="openai/gpt-oss-120b",
    api_key=api_key,
    temperature=0,
    # Keep retries in one place so rate-limit backoff is not multiplied by
    # ChatGroq's internal retry loop.
    max_retries=0,
)

planner_llm = llm.with_structured_output(
    Plan,
    method="json_mode",
)

NON_TEXT_EXTENSIONS = {
    ".7z", ".aac", ".avi", ".avif", ".bmp", ".db", ".doc",
    ".docx", ".eot", ".exe", ".flac", ".gif", ".gz", ".ico",
    ".jpeg", ".jpg", ".m4a", ".mkv", ".mov", ".mp3", ".mp4",
    ".ogg", ".otf", ".pdf", ".png", ".ppt", ".pptx", ".rar",
    ".sqlite", ".tar", ".ttf", ".wav", ".webm", ".webp", ".woff",
    ".woff2", ".xls", ".xlsx", ".zip",
}


class GroqRateLimitRetriesExhausted(RuntimeError):
    """Raised when Groq stays rate limited after the configured retries."""


def invoke_with_rate_limit_retry(runnable, prompt, stage: str, max_attempts: int = 6):
    """Retry Groq rate limits with server hints and increasing backoff."""
    for attempt in range(1, max_attempts + 1):
        try:
            return runnable.invoke(prompt)
        except Exception as exc:
            if getattr(exc, "status_code", None) != 429:
                raise
            if attempt == max_attempts:
                raise GroqRateLimitRetriesExhausted(
                    f"{stage} hit Groq's rate limit {max_attempts} times. "
                    "Wait for the token-per-minute window to clear, then run "
                    "the prompt again."
                ) from exc

            delay = 0.0
            response = getattr(exc, "response", None)
            headers = getattr(response, "headers", {})
            retry_after = headers.get("retry-after") if headers else None
            if retry_after is not None:
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    delay = 0.0

            if delay <= 0:
                match = re.search(
                    r"try again in ([0-9.]+)s", str(exc), re.IGNORECASE
                )
                if match:
                    delay = float(match.group(1))

            # A short provider hint can still be too brief for a busy TPM
            # window. Increase the wait on each repeat instead of hammering
            # the same request every couple of seconds.
            delay = max(delay, min(2 ** attempt, 30))
            delay = min(max(delay, 0.5), 60)
            print(
                f"[{stage}] Groq rate limit reached; retrying in "
                f"{delay:.1f} seconds (attempt {attempt + 1}/{max_attempts})."
            )
            time.sleep(delay + 0.25)


# ==================================================
# PROMPT HELPER
# Supports prompt functions that take user_prompt
# and prompt functions that take no arguments.
# ==================================================

def get_prompt(prompt_source, user_prompt=None) -> str:
    if not callable(prompt_source):
        if isinstance(prompt_source, str):
            return prompt_source
        raise TypeError("Prompt must be a string or a function.")

    signature = inspect.signature(prompt_source)

    required_parameters = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]

    if required_parameters:
        if user_prompt is None:
            raise ValueError(
                "This prompt function requires user_prompt, "
                "but no user prompt was provided."
            )
        result = prompt_source(user_prompt)
    else:
        result = prompt_source()

    if not isinstance(result, str):
        raise TypeError("The prompt function must return a string.")

    return result


# ==================================================
# WORKFLOW STATE
# ==================================================

class WorkflowState(TypedDict, total=False):
    user_prompt: str
    plan: Any
    task_plan: Any
    current_step_idx: int
    done: bool


# ==================================================
# PLANNER AGENT
# ==================================================

def planner_agent(state: WorkflowState) -> dict:
    user_prompt = state.get("user_prompt", "").strip()

    if not user_prompt:
        raise ValueError("Please enter a project prompt.")

    print("\n[Planner] Creating project plan...")

    # FIX: planner_prompt requires user_prompt.
    system_prompt = get_prompt(planner_prompt, user_prompt)

    response = invoke_with_rate_limit_retry(
        planner_llm,
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ],
        "Planner",
    )

    plan_data = response.model_dump()
    files = plan_data.get("files", [])

    if not files:
        raise ValueError("The planner returned no files.")

    # Ensure generated files stay within the project directory and can be
    # written as UTF-8 text by the coder agent.
    unsupported_files = []
    resolved_files = []
    for filepath in files:
        output_path = safe_path_for_project(filepath)
        resolved_files.append(output_path)
        if output_path.suffix.lower() in NON_TEXT_EXTENSIONS:
            unsupported_files.append(filepath)

    normalized_paths = [path.as_posix().casefold() for path in resolved_files]
    if len(normalized_paths) != len(set(normalized_paths)):
        raise ValueError(
            "The planner returned duplicate filenames that resolve to the "
            "same path."
        )

    if unsupported_files:
        raise ValueError(
            "The coder can only write text files. Replace binary outputs "
            f"{unsupported_files} with SVG/CSS assets or printable HTML."
        )

    print("[Planner] Files:", ", ".join(files))

    return {
        "plan": response,
        "current_step_idx": 0,
        "done": False,
    }


# ==================================================
# ARCHITECT AGENT
# ==================================================

def architect_agent(state: WorkflowState) -> dict:
    plan = state.get("plan")

    if plan is None:
        raise ValueError("The planner did not produce a project plan.")

    plan_data = (
        plan.model_dump()
        if hasattr(plan, "model_dump")
        else plan
    )

    expected_files = list(plan_data.get("files", []))

    if not expected_files:
        raise ValueError("The project plan contains no files.")

    prompt = f"""
You are a senior software architect.

Convert the project plan into implementation steps.

PROJECT PLAN:
{json.dumps(plan_data, ensure_ascii=False)}

Return ONLY a valid JSON object in this format:
{{
  "plan": null,
  "implementation_steps": [
    {{
      "filepath": "index.html",
      "task_description": "Detailed implementation instructions for this file."
    }}
  ]
}}

RULES:
- Create exactly one step for every requested file.
- Use exactly these filenames:
{json.dumps(expected_files)}
- Every step must contain filepath and task_description.
- Each step must be a separate JSON object.
- Do not rename files or add extra files.
- Keep all HTML, CSS and JavaScript references consistent.
- Return valid JSON only, without Markdown fences or commentary.
"""

    last_error = None

    for attempt in range(1, 4):
        try:
            print(
                f"\n[Architect] Creating implementation plan "
                f"(attempt {attempt}/3)..."
            )

            response = invoke_with_rate_limit_retry(llm, prompt, "Architect")
            raw = response.content

            if isinstance(raw, list):
                raw = "".join(
                    item.get("text", "")
                    for item in raw
                    if isinstance(item, dict)
                    and item.get("type") == "text"
                )

            if not isinstance(raw, str) or not raw.strip():
                raise ValueError("The architect returned an empty response.")

            raw = raw.strip()

            if raw.startswith("```"):
                lines = raw.splitlines()[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()

            data = json.loads(raw)
            task_plan = TaskPlan.model_validate(data)

            steps = task_plan.implementation_steps
            actual_files = [step.filepath for step in steps]

            if not steps:
                raise ValueError("The architect returned no implementation steps.")

            if (
                len(actual_files) != len(expected_files)
                or set(actual_files) != set(expected_files)
            ):
                raise ValueError(
                    f"Expected files {expected_files}; received {actual_files}."
                )

            for filepath in actual_files:
                safe_path_for_project(filepath)

            step_by_path = {step.filepath: step for step in steps}

            task_plan.implementation_steps = [
                step_by_path[filepath] for filepath in expected_files
            ]

            print("[Architect] Implementation steps:")
            for number, step in enumerate(
                task_plan.implementation_steps, start=1
            ):
                print(f"  {number}. {step.filepath}")

            return {
                "task_plan": task_plan,
                "current_step_idx": 0,
                "done": False,
            }

        except Exception as exc:
            if isinstance(exc, GroqRateLimitRetriesExhausted):
                raise
            last_error = exc
            print(f"[Architect] Attempt {attempt}/3 failed: {exc}")

            prompt += f"""

The previous response failed validation:
{exc}

Try again. Return one complete valid JSON object.
Use exactly the requested filenames and separate steps.
"""

    raise RuntimeError(
        f"Architect failed after 3 attempts. Last error: {last_error}"
    )


# ==================================================
# CODER AGENT
# ==================================================

def coder_agent(state: WorkflowState) -> dict:
    task_plan = state.get("task_plan")

    if task_plan is None:
        raise ValueError("The architect did not produce a task plan.")

    steps = task_plan.implementation_steps
    index = state.get("current_step_idx", 0)

    if index >= len(steps):
        return {"done": True}

    step = steps[index]
    filepath = step.filepath

    plan = state.get("plan")
    plan_data = (
        plan.model_dump()
        if hasattr(plan, "model_dump")
        else plan
    )

    print(
        f"\n[Coder] Implementing file {index + 1}/{len(steps)}: "
        f"{filepath}"
    )

    existing_content = read_file.invoke({"path": filepath})

    prompt = f"""
PROJECT REQUEST:
{state.get("user_prompt", "")}

PROJECT PLAN:
{json.dumps(plan_data, ensure_ascii=False)}

FILE TO CREATE:
{filepath}

IMPLEMENTATION INSTRUCTIONS:
{step.task_description}

EXISTING CONTENT:
{existing_content if existing_content else "(No existing content)"}

REQUIREMENTS:
- Return the complete content of this file only.
- Do not include explanations or Markdown code fences.
- Do not generate another file's content.
- Use the exact filenames from the project plan.
- HTML must link to the correct CSS and JavaScript files.
- CSS selectors must match the HTML.
- JavaScript must work with the generated HTML.
- Build a polished, responsive and functional interface.
- Do not leave unfinished functions or placeholder sections.
"""

    # Supports coder_system_prompt whether it takes zero arguments
    # or requires the user's prompt.
    system_prompt = get_prompt(
        coder_system_prompt,
        state.get("user_prompt", ""),
    )

    response = invoke_with_rate_limit_retry(
        llm,
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=prompt),
        ],
        "Coder",
    )

    content = response.content

    if isinstance(content, list):
        content = "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
        )

    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"The model returned empty content for {filepath}.")

    content = content.strip()

    if content.startswith("```"):
        lines = content.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    output_path = safe_path_for_project(filepath)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content + "\n", encoding="utf-8")

    saved_content = read_file.invoke({"path": filepath})

    if not saved_content.strip():
        raise RuntimeError(f"File verification failed: {filepath}")

    print(f"[Coder] Saved: {output_path}")

    next_index = index + 1

    return {
        "current_step_idx": next_index,
        "done": next_index >= len(steps),
    }


# ==================================================
# WORKFLOW ROUTING
# ==================================================

def route_after_coding(state: WorkflowState) -> str:
    if state.get("done", False):
        return "end"

    task_plan = state.get("task_plan")

    if task_plan is None:
        raise ValueError("No task plan exists.")

    if state.get("current_step_idx", 0) >= len(
        task_plan.implementation_steps
    ):
        return "end"

    return "coder"


# ==================================================
# BUILD LANGGRAPH WORKFLOW
# ==================================================

def build_graph():
    init_project_root()

    workflow = StateGraph(WorkflowState)

    workflow.add_node("planner", planner_agent)
    workflow.add_node("architect", architect_agent)
    workflow.add_node("coder", coder_agent)

    workflow.add_edge(START, "planner")
    workflow.add_edge("planner", "architect")
    workflow.add_edge("architect", "coder")

    workflow.add_conditional_edges(
        "coder",
        route_after_coding,
        {
            "coder": "coder",
            "end": END,
        },
    )

    return workflow.compile()


agent = build_graph()
