# tokengo

[English](README.md)

tokengo 是一组本地、只读的 [Agent Skills](https://agentskills.io)，用来理解 AI 编程助手如何消耗上下文和 token。技能放在 `skills/` 目录，可用标准 skills CLI 安装。

第一个技能是 [token-usage-advisor](skills/token-usage-advisor)：分析 Codex 与 Claude Code 的用量，并提供实验性的 Cursor 活动支持。它把确定性记账和调用方 Agent 的语义判断分开，最终生成可离线打开的 HTML 诊断报告。

## 安装

```bash
npx skills add yugasun/tokengo -g
```

`-g` 安装到用户级技能目录，跨项目可用。省略该参数则安装到当前项目。

只安装某一个技能：

```bash
npx skills add yugasun/tokengo --skill token-usage-advisor -g
```

也可以手动拷贝或软链接技能目录，例如：

```bash
git clone git@github.com:yugasun/tokengo.git
ln -s "$(pwd)/tokengo/skills/token-usage-advisor" ~/.codex/skills/token-usage-advisor
```

## 技能

| 技能 | 作用 |
| --- | --- |
| [`token-usage-advisor`](skills/token-usage-advisor) | 诊断当前 Agent 平台的 token 用量，并生成离线 HTML 报告 |

`token-usage-advisor` 回答：

- 哪些会话和任务形态贡献了观察到的用量？
- 高模型 / 高 effort 是否有任务难度和质量结果支撑？
- 重复工具调用、过大上下文或返工是否在增加成本？
- 下一步应尝试哪一种配置，以及何时升级？

工具报告的是实际涉及的 token，而不是承诺可节省的数量。缺失就是缺失。Cursor 仍是实验性数据源，不会用字符数去估算精确 token。

## 开发

每个技能都是自包含的。在 `skills/token-usage-advisor` 下：

```bash
PYTHONPATH=scripts python3 -m unittest discover -s scripts -p 'test*.py'
python3 scripts/token_usage.py --help
```

CLI 只使用 Python 3.11+ 标准库，不访问网络，也不读取凭据。不要提交本地会话日志、生成的报告、账号标识或密钥。

新增或修改技能见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

[MIT](LICENSE)
