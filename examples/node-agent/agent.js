// 你的單位：入口定義 + 每個入口要做的事。改這個檔案。
//
// 平台怎麼呼叫它、它怎麼回報，都在 zentra-agent.js 裡（照 specs/runtime.md 實作），
// 一般不用動那邊。

export const manifest = { name: "node-agent", version: "1.0.0" };

export const entrypoints = [
  {
    name: "default",
    description: "跟指定的人打招呼，回傳問候語",
    input_schema: {
      type: "object",
      required: ["name"],
      properties: { name: { type: "string", minLength: 1 } },
    },
    output_schema: {
      type: "object",
      required: ["greeting"],
      properties: { greeting: { type: "string" } },
    },
  },
  {
    name: "word_count",
    description: "計算一段文字有幾個字、幾個英文單字",
    input_schema: {
      type: "object",
      required: ["text"],
      properties: { text: { type: "string" } },
    },
    output_schema: {
      type: "object",
      required: ["characters", "words"],
      properties: {
        characters: { type: "integer" },
        words: { type: "integer" },
      },
    },
  },
];

// 每個入口一個函式：收 input 與 ctx，回傳 output 物件。
// ctx 提供 log、emit、step、llm、http，全部經 Gateway，容器裡沒有任何金鑰。
export const handlers = {
  async default(input, ctx) {
    await ctx.step("greet", async () => {
      await ctx.log("info", "greeting", { name: input.name });
    });
    const prefix = ctx.config.GREETING_PREFIX ?? "你好";
    return { greeting: `${prefix}，${input.name}！` };
  },

  async word_count(input, ctx) {
    const text = input.text;
    const words = text.split(/\s+/).filter((w) => /[A-Za-z0-9]/.test(w)).length;
    await ctx.emit("counted", { length: text.length });
    return { characters: [...text].length, words };
  },
};
