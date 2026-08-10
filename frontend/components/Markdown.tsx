"use client";

import React from "react";

/**
 * 경량 마크다운 렌더러 — AI 리서치 답변용.
 * 외부 의존성 없이 제한된 문법만 지원: ##/### 제목, 굵게/기울임/인라인 코드,
 * 링크, -/숫자 목록, 인용, 구분선, 파이프 표. (프롬프트에서 이 범위로 출력을 제한함)
 */

function renderInline(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)\s]+\)|\*[^*\n]+\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) {
      nodes.push(<strong key={i++} className="font-semibold text-white">{tok.slice(2, -2)}</strong>);
    } else if (tok.startsWith("`")) {
      nodes.push(
        <code key={i++} className="px-1 py-0.5 rounded bg-gray-700/70 text-amber-200 text-[0.85em]">
          {tok.slice(1, -1)}
        </code>
      );
    } else if (tok.startsWith("[")) {
      const mm = tok.match(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/);
      if (mm) {
        nodes.push(
          <a key={i++} href={mm[2]} target="_blank" rel="noreferrer"
             className="text-blue-400 hover:underline break-all">
            {mm[1]}
          </a>
        );
      } else nodes.push(tok);
    } else {
      nodes.push(<em key={i++}>{tok.slice(1, -1)}</em>);
    }
    last = m.index + tok.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function parseTableRow(line: string): string[] {
  return line.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
}

export default function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const out: React.ReactNode[] = [];
  let key = 0;
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) { i++; continue; }

    // 표: | 헤더 | + 다음 줄이 구분선(---)
    if (trimmed.startsWith("|") && i + 1 < lines.length && /^\|?[\s:|-]+\|?$/.test(lines[i + 1].trim()) && lines[i + 1].includes("-")) {
      const header = parseTableRow(trimmed);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        rows.push(parseTableRow(lines[i].trim()));
        i++;
      }
      out.push(
        <div key={key++} className="overflow-x-auto my-3">
          <table className="text-sm border-collapse min-w-[50%]">
            <thead>
              <tr>
                {header.map((h, j) => (
                  <th key={j} className="border border-gray-700 bg-gray-800 px-3 py-1.5 text-left text-gray-300 font-medium">
                    {renderInline(h)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>
                  {r.map((c, ci) => (
                    <td key={ci} className="border border-gray-700 px-3 py-1.5 text-gray-200">
                      {renderInline(c)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      continue;
    }

    // 제목
    const heading = trimmed.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      const level = heading[1].length;
      const cls =
        level <= 2
          ? "text-base font-bold text-white mt-5 mb-2 pb-1 border-b border-gray-700"
          : "text-sm font-semibold text-gray-100 mt-4 mb-1.5";
      out.push(<div key={key++} className={cls}>{renderInline(heading[2])}</div>);
      i++;
      continue;
    }

    // 구분선
    if (/^(-{3,}|\*{3,})$/.test(trimmed)) {
      out.push(<hr key={key++} className="border-gray-700 my-4" />);
      i++;
      continue;
    }

    // 인용
    if (trimmed.startsWith(">")) {
      const quote: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) {
        quote.push(lines[i].trim().replace(/^>\s?/, ""));
        i++;
      }
      out.push(
        <blockquote key={key++} className="border-l-2 border-gray-600 pl-3 my-2 text-sm text-gray-400">
          {renderInline(quote.join(" "))}
        </blockquote>
      );
      continue;
    }

    // 목록 (비순서/순서 — 단일 레벨)
    const isUl = (s: string) => /^[-*]\s+/.test(s.trim());
    const isOl = (s: string) => /^\d+[.)]\s+/.test(s.trim());
    if (isUl(trimmed) || isOl(trimmed)) {
      const ordered = isOl(trimmed);
      const test = ordered ? isOl : isUl;
      const items: string[] = [];
      while (i < lines.length && test(lines[i])) {
        items.push(lines[i].trim().replace(ordered ? /^\d+[.)]\s+/ : /^[-*]\s+/, ""));
        i++;
      }
      const Tag = ordered ? "ol" : "ul";
      out.push(
        <Tag key={key++} className={`my-2 pl-5 flex flex-col gap-1 text-sm text-gray-200 leading-relaxed ${ordered ? "list-decimal" : "list-disc"}`}>
          {items.map((it, j) => <li key={j}>{renderInline(it)}</li>)}
        </Tag>
      );
      continue;
    }

    // 문단 (빈 줄까지 병합)
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() &&
           !/^(#{1,4}\s|[-*]\s|\d+[.)]\s|>|\|)/.test(lines[i].trim()) &&
           !/^(-{3,}|\*{3,})$/.test(lines[i].trim())) {
      para.push(lines[i].trim());
      i++;
    }
    if (para.length === 0) { i++; continue; }
    out.push(
      <p key={key++} className="text-sm text-gray-200 leading-relaxed my-2">
        {renderInline(para.join(" "))}
      </p>
    );
  }

  return <div>{out}</div>;
}
