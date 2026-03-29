用户输入：{{user_input}}

如果只是聊天、寒暄、问答、讨论方案，返回 conversation。
如果明确要求读取、列出、编辑、删除、移动、创建、总结工作区资源，返回 codex。

示例：
- 输入：你好
  输出：{"route":"conversation"}
- 输入：读取 README.md
  输出：{"route":"codex"}
