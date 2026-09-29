import { describe, expect, it } from "vitest";
import { inline, parseAnswer } from "./ai-text";

describe("inline", () => {
  it("делит по **жирному**", () => {
    expect(inline("a **b** c")).toEqual([
      { text: "a ", bold: false },
      { text: "b", bold: true },
      { text: " c", bold: false },
    ]);
  });
  it("незакрытые ** не роняют разбор", () => {
    expect(inline("текст **без конца").map((s) => s.text).join("")).toBe("текст без конца");
  });
});

describe("parseAnswer", () => {
  it("абзацы, список и пустые строки", () => {
    const blocks = parseAnswer("**Заголовок**\n\nПервый абзац\nпродолжение.\n\n- пункт 1\n- пункт 2\n\nИтог");
    expect(blocks.map((b) => b.kind)).toEqual(["p", "p", "ul", "p"]);
    const list = blocks[2];
    expect(list.kind === "ul" && list.items.length).toBe(2);
    const para = blocks[1];
    expect(para.kind === "p" && para.segs.map((s) => s.text).join("")).toBe("Первый абзац продолжение.");
  });
  it("маркеры *, • и вложенный текст", () => {
    const blocks = parseAnswer("* раз\n• два");
    expect(blocks).toHaveLength(1);
    expect(blocks[0].kind).toBe("ul");
  });
  it("HTML остаётся обычным текстом, а не разметкой", () => {
    const [b] = parseAnswer('<img src=x onerror="alert(1)"> и <script>x</script>');
    expect(b.kind === "p" && b.segs.map((s) => s.text).join("")).toContain("<script>");
  });
  it("пустой текст → нет блоков; Windows-переводы строк", () => {
    expect(parseAnswer("")).toEqual([]);
    expect(parseAnswer("a\r\n\r\nb")).toHaveLength(2);
  });
});
