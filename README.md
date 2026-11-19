# 验货报告工作台

本机运行的 FHL 报告工具。导入备货单 PDF 后，核对 SQC 报告并导出 Excel，或整理装柜照片并导出 Word。

## 下载试用

在 [GitHub Releases](https://github.com/Kongmei-ovo/inspection-report-workbench/releases) 下载 Windows x64、macOS Apple 芯片或 macOS Intel 的 ZIP。完整解压后，在 Windows 打开 `InspectionWorkbench.exe`，在 Mac 打开「启动工作台.command」。

[完整操作指南](docs/操作指南.md)

## 从源码运行

需要 Python 3.11 或更新版本。在项目目录执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app/server.py
```

Windows 将 `python3` 换成 `py -3`，将 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`。启动时会打开本机浏览器。可用 `--no-browser --port 8765` 指定仅启动服务及端口。

## 构建和发布

```sh
python -m pip install -r requirements-build.txt
python scripts/build.py
python scripts/smoke.py
```

构建脚本为当前平台生成 `packages/` 下的 ZIP。推送 main 或提交 PR 后，GitHub Actions 在三个平台构建和验证。推送 `v*` 标签后，所有构建通过才将 ZIP 自动发布到 GitHub Releases。测试资料由脚本合成。
