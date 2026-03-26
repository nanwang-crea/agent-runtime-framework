change_and_verify workflow：
- 先 resolve 修改目标。
- 优先选择最小修改 primitive：能 patch 就不要 full rewrite。
- 修改完成后尽量执行验证，再综合说明修改内容和验证结果。
- 如果验证失败，优先基于失败信息继续修复，而不是立即结束。
