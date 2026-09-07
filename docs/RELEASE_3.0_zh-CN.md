# 3.0 升级与验证说明

3.0 可读取旧版 version 1 session，保存时写为 version 2。新文件不能由 2.x
打开；需要旧版兼容时，请保留原 session 副本。新版保存冠状切片位置/显隐、脑轮廓
显隐、Surface/Volume 模式及严格胞体分类设置。

退出、切换 session、安装更新之前，如有未保存修改，会提供 Save、Discard、Cancel。
新建会话也受保护。每 30 秒写入一次未保存状态的恢复副本；通过 **File > Recover
unsaved session** 找到目录，再用 Open session 打开，以 Save session as 另存。
正常关闭会移除当前窗口自己的恢复副本。首次 30 秒之前崩溃可能还没有恢复副本。

SWC 导入会明确拒绝缺列、悬空 parent、循环拓扑和不支持的数值，仍支持合法的多 root
胞体文件。损坏轴突取消勾选并显示原因，其他神经元仍可操作；不会自动改写原 SWC。

**Settings > Strict soma labels** 禁用邻域众数推断，人工 CSV 修正仍优先。
**File > Export soma diagnostics** 导出每个胞体的坐标、原始 voxel、图谱区域、
最终区域和来源：直接命中 direct、邻域推断 neighborhood、人工覆盖 manual、
越界 outside、未分配 unassigned。邻域推断是估算值，不应作为直接命中解释。
切换严格模式会重建窗口并记为未保存修改。

新生成的 raw 图谱、身份索引和表面均放入用户应用缓存目录，原图谱旁的历史缓存保留。
**Settings > Manage atlas cache** 显示空间占用，允许清除当前图谱的表面和索引，
保留正在使用的 raw 图谱及源文件；损坏的表面缓存会自动重建。

安装器不再清空整个安装目录，只覆盖打包的应用文件；升级和卸载不主动删除用户新增的
文件。请避免把研究文件命名为应用自带文件的名称。为保留未知文件，旧版不用的应用文件
可能留在原目录，不会进行无差别清理。

更新下载在后台进行，校验 GitHub SHA-256 后先处理未保存状态，再启动安装器。
默认使用普通权限；受保护旧目录的管理员启动作为显式重试。取消会等待当前网络操作
结束：socket 超时为 5 秒，但系统 DNS 解析可能更久。

发布验收包含单元/集成测试、实际离屏渲染、独立测试安装器的安装—升级—卸载以及
用户文件保留检查。测试安装器使用独立 AppId，不改写正式软件的卸载登记。
Qt 窗口回归测试使用真实控件及离屏 PyVista 后端适配器；CI smoke 不验证实际 OpenGL，
仍需代表性大数据与真实 GPU 的交互验收。不要把实验数据或标识符提交到公开仓库。

CI 会检查锁定依赖的已知漏洞。GitHub 发布工作流配置了 Sigstore 构建来源签名，
可用 `gh attestation verify <文件> --repo orionhu99/fMOST-Brain-Viewer` 验证。
构建来源签名不等于 Windows Authenticode 签名，SmartScreen 仍可能提示；软件没有
内置可信 Windows 代码签名证书。漏洞扫描也不能排除尚未公开的漏洞。
