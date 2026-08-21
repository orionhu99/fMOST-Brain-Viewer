# Allen CCF 图谱配置

## 必需资源

fMOST Brain Viewer 使用 Allen Mouse Brain CCFv3（2017 annotation），要求同一
图谱文件夹内包含：

```text
<atlas_folder>/
├── average_template_25.nrrd
└── annotation_10.nrrd
```

安装器和 portable ZIP 不包含图谱体数据。软件只随附用于识别、校验资源的图谱 manifest
和 ontology 快照。

## 推荐方式：在软件中下载

1. 在首次运行向导选择 **Download Allen CCF atlas**。
2. 选择可写且剩余空间不少于 5.5 GB 的文件夹。
3. 保持窗口打开，等待下载和校验完成。
4. 可选择脑区表面库准备级别；也可以以后再准备缓存。

下载中的文件使用 `.part` 后缀。已校验完成的文件会跳过；服务器支持 HTTP Range 时，
中断的下载从已有位置继续。软件只进行有限次数的瞬时错误重试，并保留仍可继续的
partial 文件供下次使用。

Allen 下载服务器通过 `current-release` 别名提供这些文件。本软件版本同时固定了
CCFv3 2017 文件的大小和 SHA-256。如果 Allen 以后改变该别名，软件会明确报告图谱
身份不一致，不会静默接受另一版本；此时请安装更新后的 viewer，或选择已有且已验证的
图谱文件夹。

启用图谱前会检查 manifest 身份、文件大小、SHA-256、NRRD header、维度、体素间距、
数据类型和 raw payload 大小。未通过校验的文件绝不会被当作可用图谱。

## 使用已有图谱

选择 **Use an existing atlas folder**，然后选择包含两个必需 NRRD 的文件夹。图谱
可以位于本地磁盘或稳定的网络位置，但为了冠状面浏览和表面缓存生成，强烈建议使用
本地 SSD。

不要重命名必需文件。如果校验失败，请确认 template 为 25 µm，annotation 为
10 µm CCFv3 2017，而不是名称相近的其他分辨率或版本。

## 存储和缓存

10 µm annotation 转为适合随机读取的格式后体积很大，软件还会生成全脑和脑区表面
缓存。这些都是可以重建的派生文件，应与应用安装目录、实验项目目录分开保存。

下载或准备数据前，软件会检查写权限和磁盘空间。取消缓存准备不会删除已经完成并通过
校验的条目，下次可以继续。

## Session 与图谱身份

Session 会记录 atlas signature。使用不同图谱身份打开 session 时，软件必须先警告。
此时应重新配置正确图谱或取消，不要在不同图谱定义之间静默比较。

## 来源、条款和引用

图谱体数据和 ontology 属于 Allen Institute 内容，不适用 fMOST Brain Viewer 的
MIT License。使用前请阅读最新文件：

- [Allen Institute Terms of Use](https://alleninstitute.org/terms-of-use/)
- [Allen Institute Citation Policy](https://alleninstitute.org/citation-policy/)
- [Allen Mouse Brain CCF resources](https://download.alleninstitute.org/informatics-archive/current-release/mouse_ccf/)

只有用户主动请求下载图谱时，软件才访问 Allen 服务器；不会上传本地数据。
