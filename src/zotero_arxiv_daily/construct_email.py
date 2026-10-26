# Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
from .protocol import Paper
import math
from html import escape
from urllib.parse import urlparse


framework = """
<!DOCTYPE HTML>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    .star-wrapper {
      font-size: 1.3em; /* 调整星星大小 */
      line-height: 1; /* 确保垂直对齐 */
      display: inline-flex;
      align-items: center; /* 保持对齐 */
    }
    .half-star {
      display: inline-block;
      width: 0.5em; /* 半颗星的宽度 */
      overflow: hidden;
      white-space: nowrap;
      vertical-align: middle;
    }
    .full-star {
      vertical-align: middle;
    }
  </style>
</head>
<body>

<div>
    __CONTENT__
</div>

<br><br>
<div>
To unsubscribe, remove your email in your Github Action setting.
</div>

</body>
</html>
"""

def get_empty_html():
  block_template = """
  <table border="0" cellpadding="0" cellspacing="0" width="100%" style="font-family: Arial, sans-serif; border: 1px solid #ddd; border-radius: 8px; padding: 16px; background-color: #f9f9f9;">
  <tr>
    <td style="font-size: 20px; font-weight: bold; color: #333;">
        No Papers Today. Take a Rest!
    </td>
  </tr>
  </table>
  """
  return block_template

def get_block_html(title:str, authors:str, rate:str, tldr:str, pdf_url:str, affiliations:str=None):
    block_template = """
    <table border="0" cellpadding="0" cellspacing="0" width="100%" style="font-family: Arial, sans-serif; border: 1px solid #ddd; border-radius: 8px; padding: 16px; background-color: #f9f9f9;">
    <tr>
        <td style="font-size: 20px; font-weight: bold; color: #333;">
            {title}
        </td>
    </tr>
    <tr>
        <td style="font-size: 14px; color: #666; padding: 8px 0;">
            {authors}
            <br>
            <i>{affiliations}</i>
        </td>
    </tr>
    <tr>
        <td style="font-size: 14px; color: #333; padding: 8px 0;">
            <strong>Relevance:</strong> {rate}
        </td>
    </tr>
    <tr>
        <td style="font-size: 14px; color: #333; padding: 8px 0;">
            <strong>TLDR:</strong> {tldr}
        </td>
    </tr>

    <tr>
        <td style="padding: 8px 0;">
            <a href="{pdf_url}" style="display: inline-block; text-decoration: none; font-size: 14px; font-weight: bold; color: #fff; background-color: #d9534f; padding: 8px 16px; border-radius: 4px;">PDF</a>
        </td>
    </tr>
</table>
"""
    return block_template.format(title=title, authors=authors,rate=rate, tldr=tldr, pdf_url=pdf_url, affiliations=affiliations)

def get_stars(score:float):
    full_star = '<span class="full-star">⭐</span>'
    half_star = '<span class="half-star">⭐</span>'
    low = 6
    high = 8
    if score <= low:
        return ''
    elif score >= high:
        return full_star * 5
    else:
        interval = (high-low) / 10
        star_num = math.ceil((score-low) / interval)
        full_star_num = int(star_num/2)
        half_star_num = star_num - full_star_num * 2
        return '<div class="star-wrapper">'+full_star * full_star_num + half_star * half_star_num + '</div>'


def safe_url(value):
    return escape(value, quote=True) if value and urlparse(value).scheme in ("https", "http") else "#"


def render_email(papers:list[Paper], failures=None) -> str:
    parts = []
    if failures:
        parts.append("<p>本次检索覆盖不完整，下次将补检：" + escape("、".join(failures)) + "</p>")
    for p in papers:
        metrics = p.metrics or {}
        jcr = metrics.get("jcr") or "未知"
        year = metrics.get("year") or "年份未核实"
        metric_text = f"JCR {jcr}；IF {metrics.get('if') or '未知'}；{year}"
        if not metrics.get("jcr") and metrics.get("esci"):
            metric_text += "；ESCI 返回 " + str(metrics["esci"]) + "（JCR 口径待核实）"
        if metrics.get("source"):
            metric_text += "；来源 " + str(metrics["source"])
        if metrics.get("stale"):
            metric_text += "；旧缓存"
        if not p.journal or p.item_type == "preprint":
            metric_text = "预印本：不适用 JCR/IF"
        rate = str(round(p.score, 1)) if p.score is not None else "未知"
        group = {"core": "核心课题", "cross": "交叉研究", "explore": "探索推荐"}.get(p.recommendation_group, "相关文献")
        citations = (f"引用 {p.citation_count}（{p.citation_source}，{p.citation_checked}）"
                     if p.citation_count is not None else "引用数暂未获取")
        authors = p.authors if len(p.authors) <= 5 else p.authors[:3] + ['...'] + p.authors[-2:]
        parts.append("<article><p>" + escape(group) + "</p><h2>" + escape(p.title) + "</h2><p>" + escape(", ".join(authors)) +
                     "</p><p>" + escape((p.published or "日期未知") + " · " + (p.journal or p.source)) +
                     "</p><p><strong>" + escape(metric_text) + "</strong></p><p>" + escape(citations) + "</p><p>相关性：" + rate +
                     "（模型相似度分数，非概率）</p><p>" + escape(p.tldr or p.abstract or "暂无摘要").replace("\n", "<br>") +
                     "</p><p>推荐原因：" + escape(p.reason) + "</p><p><a href=\"" + safe_url(p.url) + "\">论文原文</a>" +
                     (" · <a href=\"" + safe_url(p.pdf_url) + "\">PDF</a>" if p.pdf_url else "") + "</p>")
        if p.save_url and p.reject_url:
            parts.append('<p><a href="' + safe_url(p.save_url) + '">保存到 Zotero</a> · <a href="' + safe_url(p.reject_url) + '">不感兴趣</a>（打开后需确认）</p>')
        else:
            parts.append("<p>预览模式：收藏操作未启用。</p>")
        # Keep affiliation data if supplied, without another LLM call.
        if p.affiliations:
            parts.append("<p>" + escape(", ".join(p.affiliations[:5])) + (", ..." if len(p.affiliations) > 5 else "") + "</p>")
        parts.append("</article><hr>")
    if not papers:
        parts.append(get_empty_html())
    return framework.replace('__CONTENT__', "".join(parts))
