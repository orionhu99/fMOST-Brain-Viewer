# 数据格式说明

## 坐标要求

所有 SWC 坐标必须已经使用同一 Allen CCF 坐标约定完成配准，并以 µm 表示。fMOST
Brain Viewer 不估计也不应用配准变换。

## 标准项目结构

```text
<project_folder>/
├── <dataset_id>_reg_800/
│   ├── <dataset_id>-101_reg.swc
│   ├── <dataset_id>-102_reg.swc
│   └── ...
└── <dataset_id>/
    └── soma location/
        ├── <dataset_id>_root_reg.swc
        └── soma location_<dataset_id>.csv   # 可选
```

`<dataset_id>` 是稳定的任意字符串，例如 `sample_A` 或 `pilot-02`，不要求是纯数字。
axon 目录、soma 目录和 soma 文件名应使用相同标识。

## SWC 文件

解析器接受标准 SWC 行：

```text
node_id type x_um y_um z_um radius parent_id
```

空行和以 `#` 开头的行会被忽略。soma 文件包含胞体 node ID 和配准坐标；每个 axon
文件包含一条重建神经元的节点和 parent 连接。

所有输入均只读，软件不会改写 SWC。

## 严格 axon–soma 匹配

导入每个 axon 时，软件从文件名提取数字 token，并与该数据集 soma SWC 的 node ID
比较。只有能唯一识别出一个有效 soma ID 时才接受绑定。

假设有效 soma ID 为 `101` 和 `102`：

| Axon 文件名 | 结果 |
|---|---|
| `sample_A-101_reg.swc` | 与 soma `101` 匹配 |
| `sample_A-neuron_102_reg.swc` | 与 soma `102` 匹配 |
| `sample_A-neuron_unknown_reg.swc` | unmatched |
| `sample_A-101-copy-102_reg.swc` | 有歧义，unmatched |

如果两个 axon 文件解析到同一 soma ID，导入摘要会报告 duplicate。Unmatched 和
duplicate axon 仍可查看，但不会按字母顺序、文件顺序或行号静默绑定，因此不会错误
加入 soma 脑区分组，也不会生成绑定 soma 点。

每次准备新数据后都应检查导入摘要。

## 可选的人工脑区校正

将 `soma location_<dataset_id>.csv` 放在 soma SWC 同目录。每行把 soma node ID
映射到 Allen structure acronym、structure ID 或未归属状态：

```csv
soma_id,region
101,MOp
102,VISp_r
103,unassigned
```

表头可省略。为了兼容人工核对表，可使用 `_l`、`_r`、`-left`、`-right` 后缀；软件
会归一化到对应的 Allen 基础结构，但不会根据后缀推断半球身份。未知 soma ID、未知
脑区、格式错误或重复校正会在导入摘要中报告或忽略，不会修改源 CSV。

## 多数据集

每个神经元在软件内部使用 dataset key 和 neuron ID 共同标识，因此不同项目可以安全
使用相同 soma 编号。保存 session 时应保持 dataset ID 和项目位置稳定。同一路径的同一
项目不会被重复导入；不同路径下的同名 dataset 保持独立，并用路径区分。

## 不应提交的文件

实验 SWC/CSV/NRRD、session、截图、GIF、日志和派生表面缓存不应提交到源码仓库。
公开测试会在运行时生成微小 synthetic fixture，并在结束后删除。
