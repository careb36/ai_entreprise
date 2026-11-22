Changed

* **The Developer's tool rounds default to 5, measured (issue #766).** Phase D ran the bench's specs 01 and 02 with the best DeepSeek model on every role and with Qwen on the Developer, a million tokens each. With 8 rounds a third of the tool runs used every round and were verified no more often, and neither arm fit both specs; with 5 both did, every criterion passed, no task was blocked, and spec 01 cost 464 000 tokens instead of 694 000 (DeepSeek) and 367 000 instead of 891 000 (Qwen). `DEVELOPER_TOOL_ROUNDS` (and a company's `tool_rounds`, 1–50) still sets it.
