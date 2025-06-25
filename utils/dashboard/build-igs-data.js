// generate-igs.mjs
import fs from "fs";
import yaml from "js-yaml";
import fetch from "node-fetch";

const token = process.env.GH_TOKEN;
if (!token) {
  console.error("❌ GH_TOKEN not set");
  process.exit(1);
}

const headers = {
  Authorization: `Bearer ${token}`,
  "Content-Type": "application/json",
};

async function main() {
  const config = yaml.load(fs.readFileSync("./public/igs.yaml", "utf8"));
  const result = [];

  for (const ig of config.igs) {
    const [owner, repo] = ig.repo.split("/");

    const query = `
    {
      repository(owner: "${owner}", name: "${repo}") {
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
    const repoData = json.data?.repository;

    if (!repoData) {
      console.warn(`⚠️  Skipping ${ig.repo}`);
      continue;
    }

    const defaultBranch = repoData.defaultBranchRef?.name || "";
    const defaultCommit = repoData.defaultBranchRef?.target?.committedDate || "";

    const branches = (repoData.branches?.nodes || [])
      .filter((n) => n.name !== "gh-pages")
      .map((node) => ({
        name: node.name,
        commit: node.target?.committedDate,
      }));

    const tags = (repoData.tags?.nodes || [])
      .filter((n) => !["current", "vcurrent"].includes(n.name.toLowerCase()))
      .filter((n) => n.target?.committedDate) // only tags with commit date
      .sort((a, b) => new Date(b.target.committedDate) - new Date(a.target.committedDate))      
      .map((n) => n.name);

    result.push({
      name: ig.name,
      repo: ig.repo,
      published: ig.published || null,
      html_url: repoData.url,
      default_branch: defaultBranch,
      last_commit: defaultCommit,
      branches,
      tags,
    });
  }

  fs.mkdirSync("./public", { recursive: true });
  fs.writeFileSync("./public/igs-data.json", JSON.stringify(result, null, 2));
  console.log("✅ JSON saved to public/igs.json");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
