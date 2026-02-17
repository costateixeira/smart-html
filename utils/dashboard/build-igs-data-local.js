// build-igs-data-local.js
// Builds igs-data.json from local smart-html folder content (no GitHub token needed)

import fs from "fs";
import path from "path";
import yaml from "js-yaml";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SMART_HTML_ROOT = path.resolve(__dirname, "../..");

function findLocalFolder(repoName) {
  // repoName is e.g. "smart-trust" or "ddcc"
  // Try exact match first, then strip "smart-" prefix
  const candidates = [repoName, repoName.replace(/^smart-/, "")];
  for (const name of candidates) {
    const dir = path.join(SMART_HTML_ROOT, name);
    if (fs.existsSync(dir) && fs.statSync(dir).isDirectory()) {
      return dir;
    }
  }
  return null;
}

function readPackageList(folder) {
  const pkgPath = path.join(folder, "package-list.json");
  if (!fs.existsSync(pkgPath)) return null;
  try {
    return JSON.parse(fs.readFileSync(pkgPath, "utf8"));
  } catch {
    return null;
  }
}

function findVersionDirs(folder) {
  // Look for subdirectories that look like version numbers (e.g. 1.0.0, 0.2.0)
  try {
    return fs
      .readdirSync(folder, { withFileTypes: true })
      .filter((d) => d.isDirectory() && /^v?\d+\.\d+/.test(d.name))
      .map((d) => d.name);
  } catch {
    return [];
  }
}

function getDirSizeBytes(dir) {
  let total = 0;
  try {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        total += getDirSizeBytes(full);
      } else {
        try { total += fs.statSync(full).size; } catch { /* skip */ }
      }
    }
  } catch { /* skip unreadable dirs */ }
  return total;
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function main() {
  const configPath = path.join(__dirname, "public", "igs.yaml");
  const config = yaml.load(fs.readFileSync(configPath, "utf8"));
  const result = [];

  for (const ig of config.igs) {
    const repoName = ig.repo.split("/")[1]; // e.g. "smart-trust"
    const [owner] = ig.repo.split("/");
    const folder = findLocalFolder(repoName);

    if (!folder) {
      console.warn(`⚠️  No local folder for ${ig.repo}, skipping`);
      continue;
    }

    const pkgList = readPackageList(folder);
    const versionDirs = findVersionDirs(folder);

    // Calculate folder sizes
    const totalSizeBytes = getDirSizeBytes(folder);
    const versionSizes = {};
    for (const vd of versionDirs) {
      versionSizes[vd] = getDirSizeBytes(path.join(folder, vd));
    }
    const versionSizesTotal = Object.values(versionSizes).reduce((a, b) => a + b, 0);
    const rootSizeBytes = totalSizeBytes - versionSizesTotal;

    console.log(`  ${ig.name}: ${formatSize(totalSizeBytes)} total (root: ${formatSize(rootSizeBytes)})`);
    for (const [v, sz] of Object.entries(versionSizes)) {
      console.log(`    ${v}: ${formatSize(sz)}`);
    }

    // Build published versions from package-list.json
    const publishedVersions = [];
    const versionEntries = [];

    if (pkgList?.list) {
      for (const entry of pkgList.list) {
        const v = (entry.version || "").replace(/^v/, "");
        if (!v || ["current", "vcurrent"].includes(v.toLowerCase())) continue;

        const sizeBytes = versionSizes[v] || versionSizes[`v${v}`] || null;

        publishedVersions.push({
          version: v,
          publishedUrl: entry.path || null,
        });

        versionEntries.push({
          version: v,
          hasTag: versionDirs.includes(v) || versionDirs.includes(`v${v}`),
          publishedUrl: entry.path || null,
          sizeBytes,
          size: sizeBytes ? formatSize(sizeBytes) : null,
        });
      }
    }

    // Add any version dirs not in package-list
    for (const vd of versionDirs) {
      const v = vd.replace(/^v/, "");
      if (!versionEntries.find((e) => e.version === v)) {
        const sizeBytes = versionSizes[vd] || null;
        versionEntries.push({
          version: v,
          hasTag: true,
          publishedUrl: null,
          sizeBytes,
          size: sizeBytes ? formatSize(sizeBytes) : null,
        });
      }
    }

    const ciBuildUrl = `https://${owner}.github.io/${repoName}/`;

    result.push({
      name: ig.name,
      repo: ig.repo,
      published: ig.published || null,
      html_url: `https://github.com/${ig.repo}`,
      default_branch: "main",
      last_commit: "",
      branches: [{ name: "main", daysSince: 0, isDefault: true, isStale: false }],
      versions: versionEntries,
      publishedVersions,
      ciBuildUrl,
      sizeBytes: totalSizeBytes,
      size: formatSize(totalSizeBytes),
      rootSizeBytes: rootSizeBytes,
      rootSize: formatSize(rootSizeBytes),
    });
  }

  const outPath = path.join(__dirname, "public", "igs-data.json");
  fs.mkdirSync(path.join(__dirname, "public"), { recursive: true });
  fs.writeFileSync(outPath, JSON.stringify(result, null, 2));
  console.log(`✅ Wrote ${result.length} IGs to ${outPath}`);
}

main();
