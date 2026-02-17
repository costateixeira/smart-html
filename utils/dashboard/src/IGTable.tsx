/// <reference types="vite/client" />

import React, { useEffect, useState } from "react";
import { parse } from "yaml";
import "./styles.css";

const GITHUB_TOKEN = import.meta.env.VITE_GITHUB_TOKEN;

type ViewMode = "release" | "ci";

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
  sizeBytes?: number | null;
  size?: string | null;
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
  size?: string;
  sizeBytes?: number;
  rootSize?: string;
  rootSizeBytes?: number;
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
  console.warn(`No proxy for host: ${parsed.hostname}`);
  return published;
}

export default function IGTable() {
  const [igs, setIgs] = useState<IG[]>([]);
  const [view, setView] = useState<ViewMode>("release");
  const [showOldBranches, setShowOldBranches] = useState(false);
  const [showUnpublishedTags, setShowUnpublishedTags] = useState(true);
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const [dataSource, setDataSource] = useState<"local" | "live" | "">("");

  const toggleRow = (repo: string) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(repo)) next.delete(repo);
      else next.add(repo);
      return next;
    });
  };

  const getVisibleBranches = (ig: IG) =>
    showOldBranches ? (ig.branches ?? []) : (ig.branches ?? []).filter((b) => b.isDefault || !b.isStale);

  const getVisibleVersions = (ig: IG) =>
    showUnpublishedTags
      ? (ig.versions ?? [])
      : (ig.versions ?? []).filter((v) => v.publishedUrl);

  const triggerBuild = (ig: IG, branch: string) => {
    alert(`Trigger build for ${ig.name} on ${branch}`);
  };

  useEffect(() => {
    const loadData = async () => {
      // FIRST TRY LOCAL igs-data.json
      try {
        const res = await fetch("./igs-data.json");
        if (res.ok) {
          const json = await res.json();
          setIgs(
            json.map((ig: any) => {
              const publishedVersions = ig.publishedVersions ?? [];
              const versions: Version[] = ig.versions ?? Array.from(
                new Set([
                  ...(publishedVersions.map((v: any) => v.version) ?? [])
                ])
              ).map((version: string) => {
                const publishedEntry = publishedVersions.find((v: any) => v.version === version);
                return {
                  version,
                  hasTag: true,
                  publishedUrl: publishedEntry?.publishedUrl ?? null,
                };
              });

              return {
                ...ig,
                branches: ig.branches ?? [],
                versions,
                publishedVersions,
                html_url: ig.html_url ?? "",
                default_branch: ig.default_branch ?? "",
                last_commit: ig.last_commit ?? "",
                ciBuildUrl: ig.ciBuildUrl ?? "",
              } as IG;
            })
          );
          setDataSource("local");
          console.log("Loaded prebuilt igs-data.json");
          return;
        }
      } catch {
        console.log("No prebuilt igs-data.json found, using live mode");
      }

      // OTHERWISE USE LIVE GRAPHQL
      if (!GITHUB_TOKEN) {
        console.error("No GitHub token, cannot load live data");
        return;
      }

      const yamlText = await fetch("./igs.yaml").then((res) => res.text());
      const config: Config = parse(yamlText);

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
              .filter((v: string) => !["current", "vcurrent"].includes(v.toLowerCase()));

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
            console.error(`Failed to load IG [${ig.name}]:`, e);
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

      setIgs(
        allIgs.map((ig: any) => ({
          ...ig,
          branches: ig.branches ?? [],
          versions: ig.versions ?? [],
          publishedVersions: ig.publishedVersions ?? [],
          html_url: ig.html_url ?? "",
          default_branch: ig.default_branch ?? "",
          last_commit: ig.last_commit ?? "",
          ciBuildUrl: ig.ciBuildUrl ?? "",
        } as IG))
      );
      setDataSource("live");
    };

    loadData();
  }, []);

  return (
    <div className="container">
      <h1>IG Dashboard</h1>

      <div className="tabs">
        <button
          className={`tab ${view === "release" ? "active" : ""}`}
          onClick={() => setView("release")}
        >
          Release
        </button>
        <button
          className={`tab ${view === "ci" ? "active" : ""}`}
          onClick={() => setView("ci")}
          disabled={dataSource === "local"}
          title={dataSource === "local" ? "CI view requires live GitHub data (set VITE_GITHUB_TOKEN)" : ""}
        >
          CI Build
        </button>
      </div>

      {view === "ci" && (
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
      )}

      {view === "release" ? (
        /* ── RELEASE VIEW ── */
        <table className="dashboard-table">
          <thead>
            <tr>
              <th></th>
              <th>Name</th>
              <th>Repo</th>
              <th>Latest Release</th>
              <th>Published URL</th>
              <th>Disk Size</th>
            </tr>
          </thead>
          <tbody>
            {igs.map(ig => {
              const isExpanded = expandedRows.has(ig.repo);
              const versions = ig.versions ?? [];
              const pv = ig.publishedVersions ?? [];
              const latestVersion = versions[0] || null;
              const latestPublished = pv[0] || null;
              const hasMore = versions.length > 1 || pv.length > 1;

              return (
                <React.Fragment key={ig.repo}>
                  <tr className="ig-row" onClick={() => toggleRow(ig.repo)}>
                    <td className="expand-cell">
                      <span className={`expand-arrow ${isExpanded ? "expanded" : ""}`}>
                        {hasMore ? "\u25B6" : ""}
                      </span>
                    </td>
                    <td>{ig.name}</td>
                    <td>
                      <a href={ig.html_url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}>
                        {ig.repo.split("/")[1]}
                      </a>
                    </td>
                    <td>
                      {latestVersion ? (
                        <span>
                          {latestPublished ? (
                            <a href={latestPublished.publishedUrl} target="_blank" rel="noreferrer" className="green-ok" onClick={e => e.stopPropagation()}>
                              v{latestVersion.version}
                            </a>
                          ) : (
                            <span className={latestVersion.hasTag ? "green-ok" : ""}>v{latestVersion.version}</span>
                          )}
                          {latestVersion.size && <small className="size-badge">{latestVersion.size}</small>}
                          {!latestVersion.publishedUrl && <span className="warning-icon">not in package-list</span>}
                          {versions.length > 1 && (
                            <small className="more-count">+{versions.length - 1} more</small>
                          )}
                        </span>
                      ) : (
                        <span className="no-releases">No releases</span>
                      )}
                    </td>
                    <td>
                      {ig.published ? (
                        <a href={ig.published} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}>
                          {ig.published.replace(/^https?:\/\//, "")}
                        </a>
                      ) : (
                        <span className="no-releases">-</span>
                      )}
                    </td>
                    <td>
                      {ig.size ? (
                        <span className="size-total">
                          {ig.size}
                          {ig.rootSize && <small className="size-detail"> (root: {ig.rootSize})</small>}
                        </span>
                      ) : (
                        <span>-</span>
                      )}
                    </td>
                  </tr>

                  {isExpanded && hasMore && (
                    <tr className="expanded-row">
                      <td></td>
                      <td colSpan={2}>
                        <div className="expanded-section">
                          <strong>All Versions</strong>
                          <ul className="tags-list compact">
                            {versions.map(v => (
                              <li key={v.version}>
                                <span className={v.hasTag ? "green-ok" : ""}>v{v.version}</span>
                                {v.size ? <small className="size-badge">{v.size}</small> : <small className="no-local">no local copy</small>}
                                {!v.publishedUrl && <span className="warning-icon">not in package-list</span>}
                              </li>
                            ))}
                          </ul>
                        </div>
                      </td>
                      <td colSpan={3}>
                        <div className="expanded-section">
                          <strong>Published Versions</strong>
                          {pv.length > 0 ? (
                            <ul className="tags-list compact">
                              {pv.map(v => {
                                const versionInfo = (ig.versions ?? []).find(ver => ver.version === v.version);
                                return (
                                  <li key={v.version}>
                                    <a href={v.publishedUrl} target="_blank" rel="noreferrer" className="green-ok">v{v.version}</a>
                                    {versionInfo?.size ? <small className="size-badge">{versionInfo.size}</small> : <small className="no-local">no local copy</small>}
                                  </li>
                                );
                              })}
                            </ul>
                          ) : (
                            <span className="no-releases">No published versions</span>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      ) : (
        /* ── CI BUILD VIEW ── */
        <table className="dashboard-table">
          <thead>
            <tr>
              <th></th>
              <th>Name</th>
              <th>Repo</th>
              <th>Branches</th>
              <th>Default Branch</th>
              <th>Last Commit</th>
              <th>Latest Tag</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {igs.map(ig => {
              const isExpanded = expandedRows.has(ig.repo);
              const visibleVersions = getVisibleVersions(ig);
              const latestVersion = visibleVersions[0] || null;
              const olderVersions = visibleVersions.slice(1);

              return (
                <React.Fragment key={ig.repo}>
                  <tr className="ig-row" onClick={() => toggleRow(ig.repo)}>
                    <td className="expand-cell">
                      <span className={`expand-arrow ${isExpanded ? "expanded" : ""}`}>
                        {olderVersions.length > 0 ? "\u25B6" : ""}
                      </span>
                    </td>
                    <td>{ig.name}</td>
                    <td>
                      <a href={ig.html_url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}>
                        {ig.repo.split("/")[1]}
                      </a>
                    </td>
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
                      {latestVersion ? (
                        <span>
                          <span className={latestVersion.hasTag ? "green-ok" : ""}>v{latestVersion.version}</span>
                          {!latestVersion.publishedUrl && <span className="warning-icon">unpublished</span>}
                          {olderVersions.length > 0 && (
                            <small className="more-count">+{olderVersions.length} more</small>
                          )}
                        </span>
                      ) : (
                        <span className="no-releases">No tags</span>
                      )}
                    </td>
                    <td className="build-dropdown" onClick={e => e.stopPropagation()}>
                      <div className="dropdown">
                        <button className="action-btn">Build &#x23F7;</button>
                        <ul className="dropdown-menu compact">
                          {(ig.branches ?? []).map((branch) => (
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

                  {isExpanded && olderVersions.length > 0 && (
                    <tr className="expanded-row">
                      <td></td>
                      <td colSpan={3}>
                        <div className="expanded-section">
                          <strong>All Tags</strong>
                          <ul className="tags-list compact">
                            {visibleVersions.map(v => (
                              <li key={v.version}>
                                <span className={v.hasTag ? "green-ok" : ""}>v{v.version}</span>
                                {!v.publishedUrl && <span className="warning-icon">unpublished</span>}
                              </li>
                            ))}
                          </ul>
                        </div>
                      </td>
                      <td colSpan={4}>
                        <div className="expanded-section">
                          <strong>Published Versions</strong>
                          {(ig.publishedVersions?.length ?? 0) > 0 ? (
                            <ul className="tags-list compact">
                              {(ig.publishedVersions ?? []).map(v => (
                                <li key={v.version}>
                                  <a href={v.publishedUrl} target="_blank" rel="noreferrer" className="green-ok">v{v.version}</a>
                                </li>
                              ))}
                            </ul>
                          ) : (
                            <span className="no-releases">No published versions</span>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
