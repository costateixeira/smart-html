/// <reference types="vite/client" />

import React, { useEffect, useState } from "react";
import { parse } from "yaml";
import "./styles.css";

const GITHUB_TOKEN = import.meta.env.VITE_GITHUB_TOKEN;

interface Branch {
  name: string;
  daysSince: number;
  isDefault: boolean;
  isStale: boolean;
}

interface Version {
  version: string;
  hasTag: boolean;
  publishedUrl: string | null;
}

interface PublishedVersion {
  version: string;
  publishedUrl: string;
}

interface IG {
  name: string;
  repo: string;
  published?: string;
  html_url: string;
  default_branch: string;
  last_commit: string;
  branches: Branch[];
  versions: Version[];
  publishedVersions: PublishedVersion[];
  ciBuildUrl: string;
}

interface Config {
  igs: Array<{
    name: string;
    repo: string;
    published?: string;
  }>;
}

const headers = {
  Authorization: `Bearer ${GITHUB_TOKEN}`,
  "Content-Type": "application/json",
};

function getProxiedUrl(published: string): string {
  const parsed = new URL(published);
  if (parsed.hostname.includes("smart.who.int")) {
    return `/proxy/smart${parsed.pathname}/package-list.json`;
  }
  if (parsed.hostname.includes("fhir.org")) {
    return `/proxy/fhir${parsed.pathname}/package-list.json`;
  }
  if (parsed.hostname.includes("github.io")) {
    return `/proxy/githubio${parsed.pathname}/package-list.json`;
  }
  console.warn(`⚠️ No proxy for host: ${parsed.hostname}`);
  return published;
}

export default function IGTable() {
  const [igs, setIgs] = useState<IG[]>([]);
  const [showOldBranches, setShowOldBranches] = useState(false);
  const [showUnpublishedTags, setShowUnpublishedTags] = useState(true);

  const getVisibleBranches = (ig: IG) =>
    showOldBranches ? ig.branches : ig.branches.filter((b) => b.isDefault || !b.isStale);

  const getVisibleVersions = (ig: IG) =>
    showUnpublishedTags ? ig.versions : ig.versions.filter((v) => v.publishedUrl);

  const triggerBuild = (ig: IG, branch: string) => {
    alert(`Trigger build for ${ig.name} on ${branch}`);
  };

  useEffect(() => {
    const loadData = async () => {
      // ✅ FIRST TRY LOCAL igs-data.json
      try {
        const res = await fetch("./igs-data.json");
        if (res.ok) {
          const json = await res.json();
          setIgs(json);
          console.log("✅ Loaded prebuilt igs-data.json");
          return;
        }
      } catch {
        console.log("⚠️ No prebuilt igs-data.json found, using live mode");
      }

      // ✅ OTHERWISE USE LIVE GRAPHQL
      if (!GITHUB_TOKEN) {
        console.error("❌ No GitHub token, cannot load live data");
        return;
      }

      const yamlText = await fetch("./igs.yaml").then((res) => res.text());
      const config: Config = parse(yamlText);

      const now = new Date();
      const MAX_DAYS = 90;

      const allIgs = await Promise.all(
        config.igs.map(async (ig) => {
          try {
            const [owner, repoName] = ig.repo.split("/");

            const query = `{
              repository(owner: "${owner}", name: "${repoName}") {
                url
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
              }
            }`;

            const res = await fetch("https://api.github.com/graphql", {
              method: "POST",
              headers,
              body: JSON.stringify({ query }),
            });

            const result = await res.json();
            const repo = result.data?.repository;
            if (!repo) throw new Error(`Repo not found for ${ig.repo}`);

            const defaultBranchName = repo.defaultBranchRef?.name || "";
            const defaultCommitDate = repo.defaultBranchRef?.target?.committedDate
              ? new Date(repo.defaultBranchRef.target.committedDate)
              : null;

            const branches: Branch[] = (repo.branches?.nodes || [])
              .filter((node: any) => node.name !== "gh-pages")
              .map((node: any) => {
                const commitDate = new Date(node.target.committedDate);
                const daysSince = Math.floor(
                  (Date.now() - commitDate.getTime()) / (1000 * 60 * 60 * 24)
                );
                return {
                  name: node.name,
                  daysSince,
                  isDefault: node.name === defaultBranchName,
                  isStale: daysSince > MAX_DAYS && node.name !== defaultBranchName,
                };
              });

            const tagVersions = (repo.tags?.nodes || [])
              .map((n: any) => n.name.replace(/^v/, ""))
              .filter((v) => !["current", "vcurrent"].includes(v.toLowerCase()));

            let publishedEntries: PublishedVersion[] = [];
            if (ig.published) {
              try {
                const pkgUrl = getProxiedUrl(ig.published);
                const res2 = await fetch(pkgUrl);
                if (res2.ok) {
                  const json = await res2.json();
                  publishedEntries = json.list
                    .filter((e: any) =>
                      e.version &&
                      !["current", "vcurrent"].includes(e.version.toLowerCase())
                    )
                    .map((e: any) => ({
                      version: e.version.replace(/^v/, ""),
                      publishedUrl: e.path,
                    }));
                }
              } catch (e) {
                console.warn(`[${ig.name}] package-list error: ${e}`);
              }
            }

            const publishedVersions = publishedEntries.map((e) => e.version);
            const allVersions = Array.from(new Set([...tagVersions, ...publishedVersions]));

            const versions: Version[] = allVersions.map((version) => {
              const hasTag = tagVersions.includes(version);
              const publishedEntry = publishedEntries.find((e) => e.version === version);
              return {
                version,
                hasTag,
                publishedUrl: publishedEntry ? publishedEntry.publishedUrl : null,
              };
            });

            const ciBuildUrl = `https://${owner}.github.io/${repoName}/`;

            return {
              ...ig,
              html_url: repo.url,
              default_branch: defaultBranchName,
              last_commit: defaultCommitDate?.toLocaleString() || "",
              branches,
              versions,
              publishedVersions: publishedEntries,
              ciBuildUrl,
            };
          } catch (e) {
            console.error(`❌ Failed to load IG [${ig.name}]:`, e);
            return {
              ...ig,
              html_url: "",
              default_branch: "",
              last_commit: "",
              branches: [],
              versions: [],
              publishedVersions: [],
              ciBuildUrl: "",
            } as IG;
          }
        })
      );

      setIgs(allIgs);
    };

    loadData();
  }, []);

  return (
    <div className="container">
      <h1>📄 IG Dashboard</h1>
      <div className="filters">
        <label>
          <input type="checkbox" checked={showOldBranches} onChange={e => setShowOldBranches(e.target.checked)} />
          Show old branches
        </label>
        <label>
          <input type="checkbox" checked={showUnpublishedTags} onChange={e => setShowUnpublishedTags(e.target.checked)} />
          Show unpublished tags
        </label>
      </div>
      <table className="dashboard-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Repo</th>
            <th>Branches</th>
            <th>Default Branch</th>
            <th>Last Commit</th>
            <th>GitHub Tags</th>
            <th>Published Versions</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {igs.map(ig => (
            <tr key={ig.repo}>
              <td>{ig.name}</td>
              <td><a href={ig.html_url} target="_blank" rel="noreferrer">{ig.repo}</a></td>
              <td>
                <ul className="branch-list compact">
                  {getVisibleBranches(ig).map(b => (
                    <li key={b.name}>{b.name} <small>({b.daysSince} d)</small></li>
                  ))}
                </ul>
              </td>
              <td>{ig.default_branch}</td>
              <td>{ig.last_commit}</td>
              <td>
                <ul className="tags-list compact">
                  {getVisibleVersions(ig).map(v => (
                    <li key={v.version}>
                      <span className={v.hasTag ? "green-ok" : ""}>v{v.version}</span>
                      {!v.publishedUrl && <span className="warning-icon">⚠️</span>}
                    </li>
                  ))}
                </ul>
              </td>
              <td>
                {ig.publishedVersions.length ? (
                  <ul className="tags-list compact">
                    {ig.publishedVersions.map(v => (
                      <li key={v.version}>
                        <a href={v.publishedUrl} target="_blank" rel="noreferrer" className="green-ok">v{v.version}</a>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <span>No releases</span>
                )}
              </td>
              <td className="build-dropdown">
                <div className="dropdown">
                  <button className="action-btn">Trigger Build ⏷</button>
                  <ul className="dropdown-menu compact">
                    {ig.branches.map((branch) => (
                      <li
                        key={branch.name}
                        onClick={() => triggerBuild(ig, branch.name)}
                      >
                        {branch.name}
                      </li>
                    ))}
                  </ul>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
