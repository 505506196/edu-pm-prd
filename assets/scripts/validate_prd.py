#!/usr/bin/env python3
"""PRD 格式校验（v2.9 工作流 §5.2 的第一步）。

用法：
    python3 validate_prd.py [需求名/需求文档/需求名-PRD.html ...]

不带参数时，从当前目录向下查找所有 *-PRD.html 逐个校验。
退出码 0 = 全部通过；1 = 有文件未通过；2 = 没找到文件。

校验的是「机械可判定」的部分：章节独立性、固定表头与列数、
埋点命名、描述列分块与逐条换行、data-preview 覆盖、
文档级 style 位置、章节编号连续性。
视觉类检查（遮挡、字号、配色统一）仍需人工按 §4.3 / §4.6 核对。
"""
import sys
import re
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import unquote

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr"}

CORE_SECTIONS = ["项目信息", "版本记录", "需求背景", "需求目标", "详细方案"]

SCHEMAS = {
    "version-table":  ["版本", "日期", "修订人", "修订说明"],
    "scheme-table":   ["一级模块", "二级功能", "原型", "描述"],
    "overview-table": ["模块", "一级功能", "说明"],
    "tracking-table": ["序号", "埋点名", "埋点中文名", "埋点类型", "埋点参数", "参数值"],
}

CN_NUM = "一二三四五六七八九十"


class Node:
    def __init__(self, tag, attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def text(self):
        return "".join(x.text() if isinstance(x, Node) else x for x in self.children)

    def find(self, tag=None, css_class=None):
        result = []
        for child in self.children:
            if not isinstance(child, Node):
                continue
            if (tag is None or child.tag == tag) and (
                css_class is None or css_class in child.attrs.get("class", "").split()
            ):
                result.append(child)
            result.extend(child.find(tag, css_class))
        return result

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()


class Tree(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.current = self.root
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.current
        while node.parent and node.tag != tag:
            node = node.parent
        if node.parent:
            self.current = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def _section_of(root):
    """Return a function mapping a node to the nearest preceding h2/h3 heading text."""
    heads = [(n, n.text().strip()) for n in root.walk() if n.tag in ("h2", "h3")]

    def lookup(node):
        found = ""
        for n, txt in heads:
            if n is node:
                break
            if n.tag == "h2":
                found = txt
        return found

    return lookup


def validate_prd(content, path=None):
    root = Tree(content).root
    h2 = [n.text().strip() for n in root.find("h2")]
    errors = []
    base_dir = Path(path).parent if path else None

    # ---- 1. 核心章节与独立性 ----
    for name in CORE_SECTIONS:
        if not any(h.endswith(name) for h in h2):
            errors.append("缺少独立章节：" + name)
    if any("项目信息" in h and "版本记录" in h for h in h2):
        errors.append("项目信息和版本记录必须分开成两张表/两个章节")
    if any(("边界" in h or "异常处理" in h) for h in h2):
        errors.append("不设独立的异常/边界章节；边界情况写进详细方案描述列的【边界说明】")

    # ---- 2. 章节编号连续性 ----
    numbered = []
    for h in h2:
        m = re.match(r"^([一二三四五六七八九十]+)、", h)
        if m:
            numbered.append((m.group(1), h))
    expected = list(CN_NUM[: len(numbered)])
    actual = [n for n, _ in numbered]
    if actual and actual != expected:
        errors.append("章节编号不连续（应从 一 开始顺排）：实际为 " + "、".join(actual))

    # ---- 3. 固定表头与列数 ----
    for css, expect in SCHEMAS.items():
        tables = root.find("table", css)
        required = css in {"version-table", "scheme-table"} or any(
            ("数据埋点" in h if css == "tracking-table"
             else "需求概述" in h if css == "overview-table" else False)
            for h in h2
        )
        if required and not tables:
            errors.append("缺少规范表格：" + css)
        for table in tables:
            rows = table.find("tr")
            if not rows:
                errors.append(css + " 没有表头行")
                continue
            heads = [c.text().strip() for c in rows[0].children
                     if isinstance(c, Node) and c.tag == "th"]
            if heads != expect:
                errors.append(css + " 表头必须为：" + "、".join(expect) + "（实际：" + "、".join(heads) + "）")

    # ---- 4. 项目信息必须独立双列表 ----
    meta = root.find("table", "meta-table")
    if not meta:
        errors.append("项目信息应使用独立 meta-table")
    else:
        for table in meta:
            for row in table.find("tr"):
                cells = [c for c in row.children if isinstance(c, Node) and c.tag in ("td", "th")]
                if len(cells) != 2:
                    errors.append("项目信息应为双列（字段 | 值）")
                    break

    # ---- 5. 详细方案：data-preview / 原型列 / 描述列 ----
    for table in root.find("table", "scheme-table"):
        rows = table.find("tr")
        for row in rows[1:]:
            cells = [c for c in row.children if isinstance(c, Node) and c.tag == "td"]
            if not cells:
                continue
            if "data-preview" not in row.attrs:
                errors.append("详细方案每行必须带 data-preview（供双栏滚动联动）")
            if len(cells) >= 3:
                proto = cells[-2]
                has_media = bool(proto.find("img") or proto.find("iframe"))
                if not has_media:
                    errors.append("原型列必须有内容（img.proto-shot 或可交互 iframe）")
            if "desc" not in cells[-1].attrs.get("class", "").split():
                errors.append("功能行末列应为 desc 描述单元格")
        for cell in table.find("td", "desc"):
            text = cell.text()
            if not all(label in text for label in ("【页面元素】", "【交互说明】")):
                errors.append("每个描述单元格必须包含【页面元素】和【交互说明】")
            if not cell.find(css_class="desc-block"):
                errors.append("描述应使用 desc-block 分块并逐条换行（不要用 <br> 硬换行）")
            for block in cell.find(css_class="desc-block"):
                items = [n for n in block.children
                         if isinstance(n, Node) and n.tag in ("p", "ol", "ul")]
                if not items:
                    errors.append("描述分块需要独立段落或列表")
            for line in cell.find("p") + cell.find("li"):
                if len(re.findall(r"(?:^|\s|[；;])\d+[、．.]", line.text())) > 1:
                    errors.append("每个元素或交互必须独立成段（多个编号不能挤在一句里）")
            if cell.find("br"):
                errors.append("描述列不要用 <br> 硬换行，改用独立 <p>")

    # ---- 6. 埋点命名与列数 ----
    for table in root.find("table", "tracking-table"):
        rows = table.find("tr")
        for row in rows[1:]:
            cells = [c for c in row.children if isinstance(c, Node) and c.tag == "td"]
            if not cells:
                continue
            if len(cells) != 6:
                errors.append("埋点表每行必须有六列（缺的格写「待确认（用户提供参考）」，不要留空）")
                continue
            event = cells[1].text().strip()
            if not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", event) or len(event) > 48:
                errors.append("埋点名需要简短英文 snake_case：" + event)

    # ---- 7. 文档级 style 必须在 #prdContent 内（一键复制全文才带得走样式）----
    # 只做"存在性"判定：<head> 放公共骨架样式、正文样式放 <main> 内是正常分工，
    # 但 #prdContent 内必须至少有一份正文样式，否则复制出去的正文会掉格式。
    main_nodes = [m for m in root.find("main") if m.attrs.get("id") == "prdContent"]
    if not main_nodes:
        errors.append("缺少 <main id=\"prdContent\"> 正文容器")
    elif not any(m.find("style") for m in main_nodes):
        errors.append("#prdContent 内没有正文 <style>；一键复制全文会丢掉正文排版样式")

    # ---- 8. 双栏预览面板必须内建（§2.6 必做项）----
    # 上一次翻车：validator 只查了 data-preview，没查面板本身在不在，
    # 于是整套 .prototype-sidebar / 双栏预览按钮 / switchPage 全漏了照样 PASS。
    if not root.find(css_class="prototype-sidebar"):
        errors.append("缺少右侧双栏预览面板 .prototype-sidebar（§2.6 必做项）")
    ids = {n.attrs.get("id") for n in root.walk() if n.attrs.get("id")}
    if "prototypeFrame" not in ids:
        errors.append("缺少预览 iframe#prototypeFrame（双栏面板内嵌活原型）")
    if "pageSelect" not in ids and len(root.find("table", "scheme-table")) and sum(
        1 for t in root.find("table", "scheme-table") for r in t.find("tr") if "data-preview" in r.attrs
    ) > 1:
        errors.append("多原型页必须提供 select#pageSelect 切页下拉（§2.6 ⑦）")
    toggle = [n for n in root.walk() if n.attrs.get("id") == "dualPaneToggle"]
    if not toggle:
        errors.append("缺少「双栏预览」开关按钮 #dualPaneToggle（§2.6 ①）")
    elif "双栏预览" not in toggle[0].text():
        errors.append("#dualPaneToggle 关闭态文案应为「双栏预览」（打开态切「收起预览」）")
    for fn in ("toggleDualPane", "switchPage", "scalePreview"):
        if fn + "(" not in content:
            errors.append("缺少双栏预览必备函数：" + fn + "()（§2.6 ③）")
    if "initPreviewScrollSync" not in content:
        errors.append("缺少滚动联动 initPreviewScrollSync()（§2.6 ⑧ 必做）")
    if "zoomPreview" not in content or "fitPreview" not in content:
        errors.append("缺少预览缩放控件 zoomPreview()/fitPreview()（§2.6 ⑨ 必做）")
    if re.search(r"<body[^>]*class=[\"'][^\"']*dual-pane", content):
        errors.append("双栏必须默认关闭：<body> 不要带 dual-pane 类（§2.6 ①）")

    # ---- 9. 原型/流程图引用的文件必须真实存在 ----
    # 上一次翻车：原型列写着 iframe 指向原型页，但 原型截图/ 目录整个是空的，
    # 而 validator 只看"有没有 img/iframe 标签"，没看 src 指向的文件在不在。
    if base_dir:
        seen = set()
        for tag in ("img", "iframe"):
            for node in root.find(tag):
                src = (node.attrs.get("src") or "").strip()
                if not src or src.startswith(("http://", "https://", "data:", "#")):
                    continue
                src = src.split("#")[0].split("?")[0]
                if src in seen:
                    continue
                seen.add(src)
                try:
                    target = (base_dir / unquote(src)).resolve()
                except (OSError, ValueError):
                    errors.append("资源路径无法解析：" + src)
                    continue
                if not target.is_file():
                    errors.append("引用的文件不存在：" + src + "（写了引用但没产出资源）")

        # 详细方案有几行，原型截图目录就该有对应产出（截图是交付物，不是可选项）
        rows = [r for t in root.find("table", "scheme-table")
                for r in t.find("tr") if "data-preview" in r.attrs]
        shot_dir = base_dir.parent / "原型截图"
        if rows and shot_dir.is_dir():
            shots = [p for p in shot_dir.glob("*.png") if p.is_file()]
            if not shots:
                errors.append("原型截图/ 目录为空；详细方案有 %d 行界面却没有任何 PNG 产出" % len(rows))
        elif rows and not shot_dir.is_dir():
            errors.append("缺少 原型截图/ 目录；请跑 scripts/export_shots.py 导出界面截图")

    # ---- 10. 需求目标不应是任务清单 ----
    goal_tables = root.find("table", "goal-table")
    for tbl in goal_tables:
        rows = tbl.find("tr")
        heads = [c.text().strip() for c in rows[0].children
                 if isinstance(c, Node) and c.tag == "th"] if rows else []
        if heads != ["用户结果", "衡量指标", "统计口径", "预期方向", "目标值"]:
            errors.append("goal-table 表头必须为：用户结果、衡量指标、统计口径、预期方向、目标值")

    # ---- 11. 双栏预览的 CSS 布局正确性（§2.6 ②）----
    # 第 8 节查的是「函数在不在」，但函数齐全不等于缩放真的работает：CSS 这三处写错，
    # scalePreview() 照样被架空，而函数存在性检查会放行。
    #
    # 踩坑实例：某 PRD 的 .device-shell 不给宽高、iframe 写 width/height:100% ——
    # 百分比在父级无确定尺寸时无法解析，iframe 退回 replaced element 默认的
    # 300×150，再被 flex 居中，于是原型缩成小方块浮在面板中央、四周全是留白。
    # 这几项必须**限定在对应 CSS 规则块内**再查，不能全文搜：实测被自己的注释骗过——
    # 注释里写着 "flex:0 0 auto 不可省"，规则块里却漏了，全文搜照样命中、检查放行。
    if "prototype-sidebar" in content:
        wrapper_css = re.search(r"\.device-wrapper\s*\{([^}]*)\}", content)
        frame_css = re.search(r"#prototypeFrame\s*\{([^}]*)\}", content)
        if "deviceWrapper" not in content and "device-wrapper" not in content:
            errors.append("双栏预览缺少 .device-wrapper 缩放包装层（§2.6 ①）")
        elif not wrapper_css or not re.search(r"flex:\s*0\s+0\s+auto", wrapper_css.group(1)):
            errors.append(
                ".device-wrapper 规则里必须显式 flex:0 0 auto，"
                "否则被 flex 压回面板宽度、放大等于白做（§2.6 ②）")
        if not frame_css or not re.search(r"transform-origin:\s*top\s+left", frame_css.group(1)):
            errors.append("#prototypeFrame 的 transform-origin 必须为 top left（§2.6 ②）")
        if frame_css and re.search(r"(width|height):\s*100%", frame_css.group(1)):
            errors.append(
                "#prototypeFrame 不能用 width/height:100%，须由 scalePreview() 设 px"
                "——父级无确定尺寸时百分比解析失败，iframe 会塌缩成 300×150（§2.6 ③）")
        if not re.search(r"(?:const|let|var)\s+CATALOG\s*=", content):
            errors.append("双栏预览缺少 CATALOG 页面逻辑尺寸配置（w/h），无法算缩放倍率（§2.6 ③）")

    if errors:
        raise ValueError("PRD 格式检查未通过：\n" + "\n".join(dict.fromkeys(errors)))
    return True


def main(argv):
    if len(argv) > 1:
        files = [Path(p) for p in argv[1:]]
    else:
        # 默认只扫 `[需求名]/需求文档/*-PRD.html`——项目里的历史 PRD / 分享源文件 /
        # 需求挖掘目录下的旧稿不按本规范撰写，扫进来只会制造噪音。
        files = sorted(Path.cwd().glob("*/需求文档/*-PRD.html"))
        if not files:
            files = sorted(Path.cwd().glob("*-PRD.html"))
    if not files:
        print("没有找到 *-PRD.html（可显式传入路径，或在本项目根目录运行）")
        return 2

    failed = 0
    for path in files:
        try:
            validate_prd(path.read_text(encoding="utf-8"), path)
            print("PASS  " + str(path))
        except ValueError as exc:
            failed += 1
            print("FAIL  " + str(path))
            for line in str(exc).splitlines()[1:]:
                print("      " + line)
        except OSError as exc:
            failed += 1
            print("ERROR " + str(path) + " —— " + str(exc))
    print("\n共 %d 个文件，%d 个未通过。" % (len(files), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
