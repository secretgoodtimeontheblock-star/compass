// Ответ модели — простой текст с редкими **жирным** и списками «- ». Разбираем его сами и
// рисуем React-элементами: никакого dangerouslySetInnerHTML, ответ модели не может внедрить разметку.

export type Seg = { text: string; bold: boolean };
export type Block = { kind: "p"; segs: Seg[] } | { kind: "ul"; items: Seg[][] };

export function inline(text: string): Seg[] {
  const out: Seg[] = [];
  let bold = false;
  for (const part of text.split("**")) {
    if (part) out.push({ text: part, bold });
    bold = !bold;
  }
  return out;
}

const BULLET = /^\s*(?:[-*•])\s+(.*)$/;

export function parseAnswer(text: string): Block[] {
  const blocks: Block[] = [];
  let para: string[] = [];
  let list: Seg[][] | null = null;

  const flushPara = () => {
    if (para.length) blocks.push({ kind: "p", segs: inline(para.join(" ")) });
    para = [];
  };
  const flushList = () => {
    if (list) blocks.push({ kind: "ul", items: list });
    list = null;
  };

  for (const raw of text.replace(/\r\n/g, "\n").split("\n")) {
    const line = raw.trim();
    const bullet = BULLET.exec(raw);
    if (!line) {
      flushPara();
      flushList();
    } else if (bullet) {
      flushPara();
      (list ??= []).push(inline(bullet[1]));
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara();
  flushList();
  return blocks;
}
