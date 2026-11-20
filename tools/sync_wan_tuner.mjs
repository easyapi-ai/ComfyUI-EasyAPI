import { createHash } from "node:crypto";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

const source = fileURLToPath(new URL("../web/", import.meta.url));
const targetArgument = process.argv.indexOf("--target");
const target = targetArgument >= 0
  ? resolve(process.argv[targetArgument + 1])
  : resolve(source, "../../easyapi-ai-web/app/utils/wan-tuner");
const check = process.argv.includes("--check");
const names = ["wan-tuner-core.js", "wan-tuner-knowledge.json"];
const knowledge = JSON.parse(await readFile(resolve(source, names[1]), "utf8"));
const digest = createHash("sha256").update(JSON.stringify(knowledge.sections)).digest("hex");
if (knowledge.content_hash !== digest) throw new Error("The canonical knowledge content hash is stale");
if (!check) await mkdir(target, { recursive: true });
for (const name of names) {
  const data = await readFile(resolve(source, name));
  if (check) {
    const copy = await readFile(resolve(target, name));
    if (!data.equals(copy)) throw new Error("Wan tuner vendor differs: " + name);
  } else await writeFile(resolve(target, name), data);
}
console.log("Wan tuner " + knowledge.version + " " + (check ? "verified" : "synced") + " at " + target);
