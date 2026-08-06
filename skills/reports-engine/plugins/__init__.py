# -*- coding: utf-8 -*-
"""报告插件框架（Report Plugin）。

通过将每份报告封装为 plugins/<plugin_id>/ 下的自包含文件夹
（plugin.yaml + render.py + template.html.j2），实现"新增报告=新增插件文件夹"的
零代码接入。本模块提供：
  - registry: 注册表（ReportPlugin 数据结构 + 注册/查询）
  - loader:   加载器（扫描/load/reload）
"""
