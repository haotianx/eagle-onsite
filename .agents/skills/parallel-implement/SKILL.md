---
name: parallel-implement
description: Implement an agreed design completely using fresh subagents for discrete components, parallel work where possible, component tests and design checks, and local commits without pushing. Use when the user requests this component-based implementation workflow.
---

Please implement all parts of this design.
- Each discrete component should be self-contained and testable.
- Use a fresh subagent to implement each discrete component. This subagent should implement, test, and check the final result against the design. Once completed, it should commit its changes (but do not push). Then destroy the subagent.
- Subagents should work on discrete components in parallel where possible.
- Define the file structure and other shared components before spinning up subagents. Make sure that work provisioned for each subagent does not collide with work of other subagents.
- If no subagent model is specified, subagents should use GPT-6.1-Sol on medium effort without fast mode.

If you have any questions, clarify them with me before beginning implementation.
