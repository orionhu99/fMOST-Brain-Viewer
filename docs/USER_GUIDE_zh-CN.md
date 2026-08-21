# fMOST Brain Viewer 中文用户指南

## 1. 安装和启动

1. 从最新 GitHub Release 下载 Windows Setup 安装包。
2. 运行安装程序；默认按当前用户安装，不需要管理员权限。
3. 从开始菜单启动 **fMOST Brain Viewer**。
4. 首次启动时下载 Allen CCF 图谱，或选择已有图谱文件夹。

Portable ZIP 适合无法安装软件的电脑。请先把整个 ZIP 解压到可写文件夹，再运行程序；
不要直接在压缩包内启动。

## 2. 配置图谱

首次启动时二选一：

- **Download Allen CCF atlas**：下载并校验所需图谱资源。
- **Use an existing atlas folder**：使用本地的 `average_template_25.nrrd` 和
  `annotation_10.nrrd`。

所有数据集共享同一套图谱。以后可从 **File > Configure Allen CCF atlas...**
更换，更换后需要重启。磁盘空间和恢复方法见[图谱配置说明](ATLAS_SETUP_zh-CN.md)。

## 3. 打开数据

启动时可以选择数据文件夹，或打开已保存的 `.fmost-session.json`。所选文件夹可以是：

- 单个项目文件夹；
- 项目中的 axon 或 soma 子文件夹；
- 包含多个项目的上级文件夹。

发现多个完整项目时，可在列表中同时勾选一个或多个。软件假定所有输入均已配准到
相同 Allen CCF 坐标约定。运行中可使用 **File > Add brain datasets...** 和
**Remove selected dataset** 调整数据组合。

导入摘要会列出 matched、unmatched 和 duplicate 数量。分析 soma 脑区分组前应先
检查这些信息；严格绑定规则见[数据格式说明](DATA_FORMAT_zh-CN.md)。

## 4. 界面控制

### Datasets

每一行代表一个导入的数据集。取消勾选会暂时隐藏该来源的全部数据，但不会删除内部
神经元选择；重新勾选即可恢复。色块表示该数据集的 soma 颜色。

### Display

- **3D brain atlas**：显示或隐藏共享图谱。
- **Coronal annotation slice**：显示或隐藏当前 annotation 切面。
- **Show all soma locations**：打开时显示已启用数据集的所有 soma；关闭后只显示与
  当前可见 axon 严格匹配的 soma。
- **Coordinate grid and bounds**：坐标网格，默认关闭。
- **Brain-region legend**：控制 3D 窗口右上角的紧凑图例。

默认 Surface 模式启动最稳定。需要灰度 Volume 时，从
**Settings > 3D brain rendering > Volume** 选择；只有首次选择后才加载大体积数据。

### Anterior–posterior position

拖动滑杆浏览 annotation 切面。左右箭头每次移动一张；长按以受控速度连续移动，松开
后立即停止。

### Appearance

可以调节图谱透明度、axon 粗细、soma 大小和高亮脑区透明度。为防止误操作，鼠标
滚轮默认不能修改参数；需要时可在
**Settings > Enable mouse-wheel parameter adjustment** 开启。

### Brain regions

输入 acronym、结构全名或 Allen structure ID，再点击 **Add region**。中性的通用
示例为 `MOp`、`VISp`。生成父级脑区表面时会包括其后代标签。列表复选框控制脑区
显隐；**Select all** 和 **Select none** 只作用于已加入列表的脑区。

### Neurons

- **Individual / manual**：按数据集显示每个神经元，每条 axon 保持独立颜色。
- **By soma region**：跨已启用数据集按 soma 所在脑区汇总；勾选一行即可显示该组
  所有神经元。

模式切换只改变列表视图，应当立即完成。Unmatched axon 仍可在独立模式显示，但不会
被错误分配到某个 soma 脑区。

## 5. 截图和录制

点击 **Capture...** 导出当前 3D 视图。对话框默认继承当前预览状态，也可以临时选择
是否包含图谱、切面、soma、axon、脑区、网格、坐标轴和图例。默认使用无损 TIFF，
也支持 PNG、JPEG 和 BMP。

点击 **Record rotating GIF...** 生成循环 360° 动画，可选择场景内容、旋转方向、
帧数、时长、尺寸和保存位置。取消后会恢复原始相机与显隐状态。

## 6. Session

使用 **File > Save Session As...** 保存数据集路径、选择、颜色、共享显示参数、脑区
高亮和相机位置。条件允许时使用相对路径；将 session 与项目文件夹一起移动仍可保持
链接。重新打开时可以重新定位或跳过缺失项目。

Session 可能包含本机路径和数据集 ID，分享前请检查并脱敏。

## 7. 日志和支持

启动或渲染报错后，可使用 **Help > Open log folder**。日志只保存在本机，不会自动
上传。公开提交 issue 前请移除或替换本机路径、数据集 ID 等内容。

常见问题见[故障排查](TROUBLESHOOTING_zh-CN.md)。
