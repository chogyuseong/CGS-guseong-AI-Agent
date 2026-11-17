// Biome 검사(린트 + 포맷)를 WASM판으로 실행하는 스크립트.
//
// 왜 이 파일이 있나: Biome CLI(biome.exe)는 서명이 없어 이 PC의 Smart App Control이 실행을 막는다.
// 같은 Biome 엔진의 WebAssembly판(@biomejs/wasm-nodejs)을 공식 JS API(@biomejs/js-api)로 불러
// biome.json 설정 그대로 검사한다. CLI가 실행되는 PC(CI 등)에서는 `pnpm lint:cli`(biome check)를 써도 된다.
//
// 사용법: pnpm lint            검사만 한다. 문제가 있으면 종료 코드 1.
//         pnpm format          포맷을 파일에 바로 적용한 뒤 린트를 검사한다.
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { Biome } from "@biomejs/js-api/nodejs";

const ROOT = path.resolve(import.meta.dirname, "..");
// .css는 뺀다: WASM판 JS API(@biomejs/js-api 6.1.0)는 css.parser.tailwindDirectives 설정을 lint에 반영하지 않아
// Tailwind 문법(@custom-variant, @apply 등)을 오류로 본다(2026-11-16 확인). CSS는 shadcn이 만든 globals.css뿐이다.
const EXTENSIONS = new Set([".ts", ".tsx", ".js", ".mjs", ".cjs", ".mts", ".json"]);
// 이 스크립트는 .gitignore를 읽지 않으므로 git이 무시하는 생성 파일·폴더를 여기에 적는다
// (Biome CLI는 biome.json의 vcs.useIgnoreFile로 같은 파일을 건너뛴다).
// next-env.d.ts: next dev·next typegen이 다시 만드는 파일이라 포맷을 맞춰 둘 수 없다.
const ALWAYS_SKIPPED = [
  "node_modules",
  ".next",
  "test-results",
  "playwright-report",
  "blob-report",
  "next-env.d.ts",
];

// biome.json의 files.includes에서 "!경로" 항목을 제외 목록으로 읽는다.
function loadConfig() {
  const config = JSON.parse(readFileSync(path.join(ROOT, "biome.json"), "utf8"));
  const excluded = (config.files?.includes ?? [])
    .filter((pattern) => pattern.startsWith("!"))
    .map((pattern) =>
      pattern
        .slice(1)
        .replace(/^\*\*\//, "")
        .replace(/\/\*\*$/, ""),
    );
  // 파일 목록은 이 스크립트가 직접 고르므로 vcs·files 설정은 엔진에 넘기지 않는다.
  const { vcs: _vcs, files: _files, ...engineConfig } = config;
  return { engineConfig, excluded: [...ALWAYS_SKIPPED, ...excluded] };
}

function isExcluded(relativePath, excluded) {
  return excluded.some((entry) => relativePath === entry || relativePath.startsWith(`${entry}/`));
}

function listFiles(dir, excluded, found = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    const relative = path.relative(ROOT, full).split(path.sep).join("/");
    if (isExcluded(relative, excluded)) continue;
    if (entry.isDirectory()) listFiles(full, excluded, found);
    else if (EXTENSIONS.has(path.extname(entry.name))) found.push(relative);
  }
  return found;
}

function lineOf(content, diagnostic) {
  const start = diagnostic.location?.span?.[0];
  if (start === undefined) return "?";
  return Buffer.from(content).subarray(0, start).toString().split("\n").length;
}

// 파일 하나를 검사하고 문제 문장 목록을 돌려준다.
function checkFile(biome, projectKey, filePath, write) {
  const original = readFileSync(path.join(ROOT, filePath), "utf8");
  const problems = [];
  const formatted = biome.formatContent(projectKey, original, { filePath });
  let content = original;
  if (formatted.content !== original) {
    if (write) {
      writeFileSync(path.join(ROOT, filePath), formatted.content);
      content = formatted.content;
    } else problems.push(`${filePath}: 포맷이 맞지 않습니다 (pnpm format으로 고칠 수 있음)`);
  }
  const lint = biome.lintContent(projectKey, content, { filePath });
  for (const diagnostic of lint.diagnostics) {
    if (!["error", "fatal", "warning"].includes(diagnostic.severity)) continue;
    const where = `${filePath}:${lineOf(content, diagnostic)}`;
    problems.push(
      `${where} [${diagnostic.severity}] ${diagnostic.category ?? ""} ${diagnostic.description}`,
    );
  }
  return problems;
}

function main() {
  const write = process.argv.includes("--write");
  const { engineConfig, excluded } = loadConfig();
  const biome = new Biome();
  const { projectKey } = biome.openProject(ROOT);
  biome.applyConfiguration(projectKey, engineConfig);
  const files = listFiles(ROOT, excluded);
  const problems = files.flatMap((file) => checkFile(biome, projectKey, file, write));
  for (const problem of problems) console.log(problem);
  console.log(`Biome(WASM): 파일 ${files.length}개 검사, 문제 ${problems.length}개`);
  process.exitCode = problems.length > 0 ? 1 : 0;
}

main();
