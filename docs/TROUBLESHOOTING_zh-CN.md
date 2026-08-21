# 故障排查

## Windows 阻止安装程序

首个正式安装器未签名。先用同一 GitHub Release 中的 `SHA256SUMS.txt` 核对 SHA-256。
如果与官方 Release 一致，可在 SmartScreen 中选择 **More info > Run anyway**。不要对
第三方镜像来源的安装包绕过安全警告。

## 软件无法启动

1. 从开始菜单重新启动一次，不要使用旧快捷方式。
2. 使用 `--self-test` 参数运行打包后的程序并等待退出码；退出码 `0` 表示资源和
   离屏渲染检查通过。窗口版程序可能不会在终端打印文字。
3. 软件能打开时使用 **Help > Open log folder**；若无法启动，请查看
   `%LOCALAPPDATA%\fMOST Brain Viewer\logs`。
4. 报告软件版本、Windows 版本、self-test 退出码和脱敏后的日志末尾。日志会记录
   `Self-test passed` 或具体失败信息。

移除本机路径和数据集 ID 前，不要公开上传完整日志。

## 图谱校验失败

- 确认两个必需文件位于同一所选文件夹，并保留原始文件名。
- 确认 template 为 25 µm，annotation 为 10 µm CCFv3 2017。
- 不要用下载未完成的 `.part` 文件代替正式文件。
- 旧下载损坏时，重新使用软件内下载；已完成并通过校验的文件会跳过。

## 图谱下载中断

检查网络、剩余空间和目录写权限，然后在同一目录重新开始，继续有效的 partial 文件。
如果代理服务器不支持 Range，请允许软件只重新下载受影响的文件。也可以手动准备完整
图谱目录，再用 existing atlas 入口完全离线使用。

## 提示 “No complete dataset was found”

按照[数据格式说明](DATA_FORMAT_zh-CN.md)检查目录。axon 目录、soma 目录和 root soma
文件名中的 dataset ID 必须一致；axon 文件夹内至少需要一个可读 SWC。

## Axon 显示为 Unmatched

axon 文件名必须唯一指向 soma SWC 中存在的一个数字 node ID。可复制并重命名 axon
文件，使目标 ID 唯一出现后再导入。不要依赖字母顺序；同一文件名含有多个有效 ID 时，
软件会有意保持 unmatched。

## 搜索不到脑区

使用准确的 Allen acronym、结构全名或数字 structure ID。可用 `MOp`、`VISp` 等
通用结构检查。如果 ontology 不可用，从 **File > Configure Allen CCF atlas...**
检查图谱资源和本地 ontology 快照。

## 首次 Add region 较慢

第一次请求可能需要生成并缓存三维表面。可用
**File > Prepare brain region library...** 提前生成。图谱不变时，之后应直接读取已校验
缓存。

## 加载神经元较慢或内存增长

axon 在首次选择时才加载。一次选择数百个大型 SWC 仍然需要解析和创建 VTK actor；
建议尽量使用脑区分组或较小选择。隐藏 actor 使用有上限的缓存，被清理后会按需重载。
Surface 模式的启动和内存开销低于 Volume。

## 截图或 GIF 导出失败

选择有足够空间的可写目录，并降低倍数、帧数或尺寸。透明输出使用 PNG 或 TIFF；JPEG
没有 alpha channel。取消或失败后应恢复原预览状态。

## 打开 Session 时出现图谱警告

Session 的 atlas signature 与当前图谱不同。请选择创建 session 时使用的图谱，或者
取消。静默继续会使空间比较不可靠。

## 提交可复现问题

使用 GitHub bug template。提供必要时的最小 synthetic dataset、完整操作步骤、预期与
实际行为、版本、Windows 版本和脱敏 diagnostics。默认不要上传真实实验数据。
