import { writeFileSync, readFileSync } from "fs";
import { parse } from "yaml";
import fetch from "node-fetch"; // if using Node <18

const token = process.env.GH_TOKEN;
if (!token) {
  console.error("❌ GH_TOKEN not set");
  process.exit(1);
}

const headers = {
  Authorization: `Bearer ${token}`,
  "Content-Type": "application/json",
};

function getProxiedUrl(published) {
  const parsed = new URL(published);
  if (parsed.hostname.includes("smart.who.int")) {
    return `https://smart.who.int${parsed.pathname}/package-list.json`;
  }
  if (parsed.hostname.includes("fhir.org")) {
    return `https://hl7.org/fhir${parsed.pathname}/package-list.json`;
  }
  if (parsed.hostname.includes("github.io")) {
    return `https://${parsed.hostname}${parsed.pathname}/package-list.json`;
  }
  return published;
}

const config = parse(readFileSync("public/igs.yaml", "utf8"));
const igs = config.igs || [];

(async () => {
  const now = new Date();
  const MAX_DAYS = 90;
  const output = [];

  for (const ig of igs) {
    const [owner, repoName] = ig.repo.split("/");

    const query = `
    {
      repository(owner: "${owner}", name: "${repoName}") {
        defaultBranchRef {
          name
          target { ... on Commit { committedDate } }
        }
        branches: refs(refPrefix: "refs/heads/", first: 100) {
          nodes {
            name
            target { ... on Commit { committedDate } }
          }
        }
        tags: refs(refPrefix: "refs/tags/", first: 100) {
          nodes { name }
        }
        url
      }
    }`;

    const res = await fetch("https://api.github.com/graphql", {
      method: "POST",
      headers,
      body: JSON.stringify({ query }),
    });

    const json = await res.json();
    const repo = json.data.repository;
    const defaultBranch = repo.defaultBranchRef?.name || "";
    const defaultCommitDate = repo.defaultBranchRef?.target?.committedDate || "";

    const branches = (repo.branches?.nodes || [])
      .filter(n => n.name !== "gh-pages")
      .map(n => {
        const commitDate = new Date(n.target.committedDate);
        const daysSince = Math.floor((now - commitDate) / (1000 * 60 * 60 * 24));
        return {
          name: n.name,
          daysSince,
          isDefault: n.name === defaultBranch,
          isStale: daysSince > MAX_DAYS && n.name !== defaultBranch,
        };
      });

    const tagVersions = (repo.tags?.nodes || [])
      .map(n => n.name.replace(/^v/, ""))
      .filter(v => !["current", "vcurrent"].includes(v.toLowerCase()));

    let publishedEntries = [];
    if (ig.published) {
      try {
        const pkgUrl = getProxiedUrl(ig.published);
        const res2 = await fetch(pkgUrl);
        const pkg = await res2.json();
        publishedEntries = pkg.list
          .filter(e => e.version && !["current", "vcurrent"].includes(e.version.toLowerCase()))
          .map(e => ({
            version: e.version.replace(/^v/, ""),
            publishedUrl: e.path,
          }));
      } catch (err) {
        console.warn(`[${ig.name}] failed to fetch package-list.json: ${err}`);
      }
    }

    const allVersions = Array.from(new Set([...tagVersions, ...publishedEntries.map(e => e.version)]));
    const versions = allVersions.map(v => ({
      version: v,
      hasTag: tagVersions.includes(v),
      publishedUrl: publishedEntries.find(e => e.version === v)?.publishedUrl || null,
    }));

    output.push({
      name: ig.name,
      repo: ig.repo,
      published: ig.published || null,
      html_url: repo.url,
      default_branch: defaultBranch,
      last_commit: defaultCommitDate,
      branches,
      versions,
      publishedVersions: publishedEntries,
      ciBuildUrl: `https://${owner}.github.io/${repoName}/`
    });
  }

  writeFileSync("public/igs-data.json", JSON.stringify(output, null, 2));
  console.log("✅ Done. Written to public/igs-data.json");
})();
