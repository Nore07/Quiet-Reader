# 贡献指南

感谢你愿意为 QuietReader 贡献代码或建议。涉及较大功能或数据格式变化时，请先开一个 Issue 讨论范围。

## 协作流程

1. 从最新的 `main` 创建独立分支：`feat/<功能>`、`fix/<问题>` 或 `docs/<主题>`。
2. 每个分支聚焦一个问题，避免把无关改动放在同一个 Pull Request 中。
3. 提交前检查改动和运行结果。界面改动可附截图；截图不是必需项，未附图时请用文字说明用户可见变化和验证方式。涉及数据格式或书架路径的改动请说明迁移方式。
4. 推送功能分支，并向 `main` 发起 Pull Request。`main` 受保护，不能直接推送。协作者和外部贡献者的 Pull Request 需要至少一位其他有写权限的协作者审批，新增提交会使旧审批失效。仓库所有者可在 Pull Request 中绕过评审要求，但仍必须通过 Pull Request 合并。
5. 处理审查意见并解决所有对话后，由维护者合并。未经维护者确认，不要创建正式版本或修改已发布的 Release。

## 分支与提交

建议使用短而明确的英文分支名，例如 `feat/epub-search`、`fix/book-paths`。提交标题使用 `<type>(<scope>): <简短说明>`，例如：

~~~text
fix(storage): 修复迁移后的书籍路径
~~~

提交正文应说明修改原因和主要做法。不要对已推送到共享分支的提交执行强制推送或改写历史。
Codex 在功能分支上每完成一个有明确结果且可验证的阶段，必须创建一次本地提交；提交只包含该阶段相关改动。未经用户在当前任务中明确确认，不得推送远程。

## Pull Request 标题与说明

- 标题简洁概括 PR 的主要目标或行为变化；正文应自包含，让未参与讨论的人也能理解背景和范围。
- 正文说明变更目的、本次完成的事项和主要改动、实际执行的验证步骤与结果、尚未验证的内容，以及兼容性和数据影响；没有兼容性或数据影响时也请明确写明。
- 界面截图为可选项。附图能帮助评审时可以提供；未附图时，正文应以文字描述用户可见变化和验证方式。
- 按 `.github/PULL_REQUEST_TEMPLATE.md` 填写，不要只写“修复若干问题”等无法说明具体内容的概括。

## 本地运行

项目使用 Python 3.12 或更新版本和 PySide6。Windows PowerShell 下可使用项目虚拟环境：

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
~~~

提交前请在 PR 中写明实际执行过的验证步骤和结果。不要提交 `.venv`、`data`、书籍或个人配置、`build` / `dist` 输出、密钥或本地日志；Windows 软件包通过 GitHub Releases 分发。

## Contribution workflow (English)

For substantial features or data-format changes, open an Issue first. Create a focused branch from the latest `main` (`feat/<topic>`, `fix/<topic>`, or `docs/<topic>`), and use the Pull Request template. The title should summarize the main goal or behavior change. The self-contained description should explain the purpose, completed work and key changes, checks actually run and their results, anything not verified, and compatibility or data impact. Screenshots for UI changes are optional; if omitted, describe the visible behavior and verification in text. Direct pushes to the protected `main` branch are blocked. Pull Requests from collaborators and outside contributors require one approval from another collaborator with write access; new commits invalidate earlier approvals. The repository owner may bypass the review requirement only when merging through a Pull Request. Resolve review conversations before merging. Maintainers handle releases.

Use concise commit subjects such as `fix(storage): repair relocated bookshelf paths`, with a body explaining why and how. Do not force-push or rewrite history on shared branches. Use the project virtual environment described above and report the checks you actually ran. Never commit `.venv`, `data`, personal books or settings, build output, secrets, or local logs.
Codex must create a local commit on the feature branch after each clearly scoped, verifiable stage, containing only that stage's changes. Do not push unless the user explicitly authorizes it in the current task.
