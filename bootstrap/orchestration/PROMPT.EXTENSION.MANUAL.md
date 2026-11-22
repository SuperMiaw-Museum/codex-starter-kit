# Manual task notifications — conditional instructions for ChatGPT

For manual work: send START and END using
`bootstrap/orchestration/manual-task.sh start PROJECT LABEL` and
`bootstrap/orchestration/manual-task.sh end TOKEN EXIT_CODE`. Preserve the returned
token and actual task exit status, including failures. `status` lists tasks
without their tokens. If you lose the token, report it and do not retry START.
