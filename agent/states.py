
from typing import Optional
from pydantic import BaseModel, Field


class Plan(BaseModel):
    project_description: str = Field(
        description="Description of the requested project."
    )
    files: list[str] = Field(
        description="List of files required for the project."
    )
    requirements: list[str] = Field(
        description="Functional and design requirements."
    )


class ImplementationStep(BaseModel):
    filepath: str = Field(
        description="Relative path of the file to implement."
    )
    task_description: str = Field(
        description="Detailed instructions for implementing this file."
    )


class TaskPlan(BaseModel):
    plan: Optional[Plan] = None
    implementation_steps: list[ImplementationStep] = Field(
        description="Ordered implementation tasks."
    )


class CoderState(BaseModel):
    task_plan: TaskPlan
    current_step_idx: int = 0
