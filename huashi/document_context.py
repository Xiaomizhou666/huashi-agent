"""将线程附件转换为受长度限制、来源清晰的 Agent 文件上下文。"""

from __future__ import annotations

from huashi.attachments import AttachmentRecord


class DocumentContextBuilder:
    """按总字符预算构建文件问答提示，避免全文无控制进入模型。"""

    def __init__(self, *, max_total_chars: int = 12000, max_file_chars: int = 6000) -> None:
        self.max_total_chars = max_total_chars
        self.max_file_chars = max_file_chars

    def build(self, question: str, records: list[AttachmentRecord]) -> tuple[str, list[AttachmentRecord]]:
        """返回增强后的用户消息与实际采用的已解析附件列表。"""

        parsed = [record for record in records if record.parse_status == "parsed" and record.parsed_text]
        if not parsed:
            return question, []

        remaining = self.max_total_chars
        sections: list[str] = []
        used: list[AttachmentRecord] = []
        for index, record in enumerate(parsed, start=1):
            header = f"\n--- 文件 {index}：{record.filename} ---\n"
            summary = f"摘要：{record.summary}\n" if record.summary else ""
            allowance = min(self.max_file_chars, max(0, remaining - len(header) - len(summary)))
            if allowance <= 0:
                break
            excerpt = (record.parsed_text or "")[:allowance]
            sections.append(header + summary + "解析内容片段：\n" + excerpt)
            used.append(record)
            remaining -= len(header) + len(summary) + len(excerpt)
            if remaining <= 0:
                break

        filenames = "、".join(record.filename for record in used)
        prompt = f"""
【用户问题】
{question}

【用户上传文件上下文】
以下内容来自当前会话已成功解析的文件：{filenames}。
{''.join(sections)}

【文件问答规则】
1. 优先依据以上文件回答，并明确注明采用的文件名。
2. 文件没有答案时，明确说“上传文件中未找到该信息”，不得补写成文件内容。
3. 仅在解析文本确实包含页码、章节或标题时才引用这些位置。
4. 如使用模型已有知识或联网搜索补充，必须与“文件内容”分开说明。
5. 多文件回答时，说明各结论分别依据哪些文件；解析失败的文件不能作为依据。
""".strip()
        return prompt, used
