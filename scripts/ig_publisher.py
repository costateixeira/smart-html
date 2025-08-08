import os
import sys
import subprocess
import logging
import argparse
import yaml
import threading
import requests
import json
from datetime import datetime
from copy import deepcopy

try:
    import git
except ImportError:
    print("Installing GitPython...")
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'gitpython'])
    import git

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
    import tkinter.font as tkfont
except ImportError:
    tk = None

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

CONFIG_FILE = "release-config.yaml"

class ReleasePublisher:
    def __init__(self, source_dir=None, source_repo=None, source_branch=None,
                 webroot_repo=None, webroot_branch=None,
                 history_repo=None, history_branch=None,
                 sparse_dirs=None, enable_sparse_checkout=False, progress_callback=None,
                 github_token=None, enable_pr_creation=False, 
                 webroot_pr_target_branch="main", registry_pr_target_branch="master"):

        self.base_dir = os.path.abspath(os.path.dirname(__file__))
        self.source_dir = source_dir or os.path.join(self.base_dir, 'source')
        self.source_repo = source_repo
        self.source_branch = source_branch
        self.webroot_repo = webroot_repo or 'https://github.com/WorldHealthOrganization/smart-html'
        self.webroot_branch = webroot_branch
        self.history_repo = history_repo or 'https://github.com/HL7/fhir-ig-history-template'
        self.history_branch = history_branch
        self.registry_repo = 'https://github.com/FHIR/ig-registry'

        self.webroot_dir = os.path.join(self.base_dir, 'webroot')
        self.history_dir = os.path.join(self.base_dir, 'history-template')
        self.registry_dir = os.path.join(self.base_dir, 'ig-registry')
        self.package_cache = os.path.join(self.base_dir, 'fhir-package-cache')
        self.temp_dir = os.path.join(self.base_dir, 'temp')
        self.publisher_jar = os.path.join(self.base_dir, 'publisher.jar')
        
        self.sparse_dirs = sparse_dirs
        self.enable_sparse_checkout = enable_sparse_checkout
        self.progress_callback = progress_callback
        
        # GitHub PR settings - auto-detect GitHub Actions environment
        self.github_token = github_token or self.get_github_token()
        self.enable_pr_creation = enable_pr_creation
        self.webroot_pr_target_branch = webroot_pr_target_branch
        self.registry_pr_target_branch = registry_pr_target_branch
        
        # Check if running in GitHub Actions
        self.is_github_actions = os.environ.get('GITHUB_ACTIONS') == 'true'

    def get_github_token(self):
        """Get GitHub token from environment (GitHub Actions or manual)"""
        # First check for GITHUB_TOKEN (GitHub Actions default)
        token = os.environ.get('GITHUB_TOKEN')
        if token:
            self.log_progress("Using GITHUB_TOKEN from environment")
            return token
        
        # Check for custom PAT environment variable
        token = os.environ.get('GH_PAT')
        if token:
            self.log_progress("Using GH_PAT from environment")
            return token
        
        return None

    def log_progress(self, message):
        logging.info(message)
        if self.progress_callback:
            self.progress_callback(message)

    def run_command(self, cmd, shell=False):
        cmd_str = ' '.join(cmd) if isinstance(cmd, list) else cmd
        self.log_progress(f"Running: {cmd_str}")
        subprocess.run(cmd, shell=shell, check=True)

    def clone_repo(self, url, path, branch=None, use_sparse=False, sparse_dirs=None):
        if os.path.exists(path):
            self.log_progress(f"Updating existing repository: {path}")
            try:
                repo = git.Repo(path)
                repo.git.reset('--hard')
                repo.remotes.origin.pull()
            except Exception as e:
                self.log_progress(f"Warning: Failed to update {path}: {e}")
            return
            
        if use_sparse and sparse_dirs:
            self.log_progress(f"Cloning with sparse checkout: {url}")
            self.run_command(['git', 'clone', '--depth=1', '--filter=blob:none', '--sparse', url, path])
            original_cwd = os.getcwd()
            os.chdir(path)
            try:
                self.run_command(['git', 'sparse-checkout', 'init'])
                self.run_command(['git', 'sparse-checkout', 'set'] + sparse_dirs)
                self.log_progress(f"Sparse checkout configured for: {' '.join(sparse_dirs)}")
            finally:
                os.chdir(original_cwd)
        else:
            self.log_progress(f"Cloning repository: {url}")
            clone_cmd = ['git', 'clone', '--depth=1']
            if branch:
                clone_cmd += ['--branch', branch]
            clone_cmd += [url, path]
            self.run_command(clone_cmd)

    def get_repo_info_from_url(self, repo_url):
        """Extract owner and repo name from GitHub URL"""
        if 'github.com' in repo_url:
            # Handle both https and git URLs
            if repo_url.startswith('https://github.com/'):
                path = repo_url.replace('https://github.com/', '')
            elif repo_url.startswith('git@github.com:'):
                path = repo_url.replace('git@github.com:', '')
            else:
                raise ValueError(f"Unsupported repository URL format: {repo_url}")
            
            # Remove .git suffix if present
            if path.endswith('.git'):
                path = path[:-4]
            
            parts = path.split('/')
            if len(parts) >= 2:
                return parts[0], parts[1]
        
        raise ValueError(f"Could not parse GitHub repository URL: {repo_url}")

    def has_changes(self, repo_path):
        """Check if repository has uncommitted changes"""
        try:
            repo = git.Repo(repo_path)
            return repo.is_dirty() or len(repo.untracked_files) > 0
        except Exception as e:
            self.log_progress(f"Warning: Could not check repository status for {repo_path}: {e}")
            return False

    def create_branch_and_commit(self, repo_path, branch_name, commit_message):
        """Create a new branch and commit all changes"""
        try:
            repo = git.Repo(repo_path)
            
            # Create and checkout new branch
            new_branch = repo.create_head(branch_name)
            new_branch.checkout()
            
            # Add all changes
            repo.git.add(A=True)
            
            # Commit changes
            repo.index.commit(commit_message)
            
            # Push to origin
            origin = repo.remote('origin')
            origin.push(new_branch)
            
            self.log_progress(f"Created branch '{branch_name}' and pushed changes")
            return True
            
        except Exception as e:
            self.log_progress(f"Error creating branch and committing: {e}")
            return False

    def create_github_pr(self, repo_url, head_branch, base_branch, title, body):
        """Create a GitHub pull request using the GitHub API"""
        if not self.github_token:
            if self.is_github_actions:
                self.log_progress("⚠️ No GitHub token available in GitHub Actions. Ensure GITHUB_TOKEN is properly configured.")
            else:
                self.log_progress("❌ GitHub token not provided, cannot create PR")
            return False
            
        try:
            owner, repo_name = self.get_repo_info_from_url(repo_url)
            
            # Check if this is the same repo we're running in (for GitHub Actions)
            current_repo = os.environ.get('GITHUB_REPOSITORY', '')
            is_same_repo = current_repo == f"{owner}/{repo_name}"
            
            if self.is_github_actions and is_same_repo:
                self.log_progress(f"📝 Creating PR in same repository ({current_repo}) using GITHUB_TOKEN")
            
            # GitHub API endpoint
            api_url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls"
            
            # PR data
            pr_data = {
                "title": title,
                "body": body,
                "head": head_branch,
                "base": base_branch
            }
            
            # Headers with authentication
            # Use Bearer format for GitHub Actions token
            auth_header = f"Bearer {self.github_token}" if self.is_github_actions else f"token {self.github_token}"
            headers = {
                "Authorization": auth_header,
                "Accept": "application/vnd.github.v3+json",
                "Content-Type": "application/json"
            }
            
            # Create PR
            response = requests.post(api_url, json=pr_data, headers=headers)
            
            if response.status_code == 201:
                pr_data = response.json()
                pr_url = pr_data['html_url']
                self.log_progress(f"✅ Pull request created: {pr_url}")
                return True
            else:
                error_msg = response.json().get('message', 'Unknown error')
                self.log_progress(f"❌ Failed to create PR: {error_msg}")
                return False
                
        except Exception as e:
            self.log_progress(f"❌ Error creating GitHub PR: {e}")
            return False

    def create_prs_if_needed(self):
        """Create pull requests for webroot and registry if changes exist"""
        if not self.enable_pr_creation:
            self.log_progress("PR creation disabled, skipping...")
            return
            
        self.log_progress("🔍 Checking for changes to create pull requests...")
        
        # Generate timestamp for branch names
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        
        # Check webroot repository
        if self.has_changes(self.webroot_dir):
            self.log_progress("📤 Creating PR for webroot repository...")
            branch_name = f"fhir-ig-update-{timestamp}"
            commit_message = f"Update FHIR IG content - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            
            if self.create_branch_and_commit(self.webroot_dir, branch_name, commit_message):
                pr_title = f"FHIR IG Content Update - {datetime.now().strftime('%Y-%m-%d')}"
                pr_body = f"""## FHIR Implementation Guide Update

This PR contains updated content from the FHIR IG publishing process.

**Changes include:**
- Updated templates and assets
- Generated documentation
- Resource definitions

**Generated on:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**Source:** {self.source_repo if self.source_repo else 'Local build'}
**Automated:** {'Yes - GitHub Actions' if self.is_github_actions else 'No - Manual run'}
"""
                
                self.create_github_pr(
                    self.webroot_repo, 
                    branch_name, 
                    self.webroot_pr_target_branch,
                    pr_title, 
                    pr_body
                )
        else:
            self.log_progress("No changes in webroot repository, skipping PR")
        
        # Check registry repository
        if self.has_changes(self.registry_dir):
            self.log_progress("📤 Creating PR for IG registry...")
            branch_name = f"registry-update-{timestamp}"
            commit_message = f"Update IG registry - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            
            if self.create_branch_and_commit(self.registry_dir, branch_name, commit_message):
                pr_title = f"IG Registry Update - {datetime.now().strftime('%Y-%m-%d')}"
                pr_body = f"""## Implementation Guide Registry Update

This PR updates the FHIR Implementation Guide registry with latest information.

**Generated on:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**Source:** {self.source_repo if self.source_repo else 'Local build'}
**Automated:** {'Yes - GitHub Actions' if self.is_github_actions else 'No - Manual run'}
"""
                
                self.create_github_pr(
                    self.registry_repo,
                    branch_name,
                    self.registry_pr_target_branch,
                    pr_title,
                    pr_body
                )
        else:
            self.log_progress("No changes in registry repository, skipping PR")

    def prepare(self):
        self.log_progress("🔄 Preparing repositories...")
        
        if self.is_github_actions:
            self.log_progress("🤖 Running in GitHub Actions environment")
        
        self.clone_repo(self.history_repo, self.history_dir, self.history_branch)
        
        self.clone_repo(
            self.webroot_repo, 
            self.webroot_dir, 
            self.webroot_branch, 
            use_sparse=self.enable_sparse_checkout,
            sparse_dirs=self.sparse_dirs
        )
        
        self.clone_repo(self.registry_repo, self.registry_dir)

        if self.source_repo:
            self.clone_repo(self.source_repo, self.source_dir, self.source_branch)

        if not os.path.exists(self.publisher_jar):
            self.log_progress("📥 Downloading FHIR IG Publisher...")
            self.run_command([
                'curl', '-L',
                'https://github.com/HL7/fhir-ig-publisher/releases/latest/download/publisher.jar',
                '-o', self.publisher_jar
            ])

        os.makedirs(self.package_cache, exist_ok=True)

    def build(self):
        self.log_progress("🔨 Building Implementation Guide...")
        self.run_command([
            'java', '-Xmx4g', '-jar', self.publisher_jar,
            'publisher', '-ig', self.source_dir,
            '-package-cache-folder', self.package_cache
        ])

    def publish(self):
        self.log_progress("📤 Publishing Implementation Guide...")
        self.run_command([
            'java', '-Xmx4g', '-Dfile.encoding=UTF-8', '-jar', self.publisher_jar,
            '-go-publish',
            '-package-cache-folder', self.package_cache,
            '-source', self.source_dir,
            '-web', self.webroot_dir,
            '-temp', self.temp_dir,
            '-registry', os.path.join(self.registry_dir, 'fhir-ig-list.json'),
            '-history', self.history_dir,
            '-templates', os.path.join(self.webroot_dir, 'templates')
        ])

    def run(self):
        try:
            self.prepare()
            self.build()
            self.publish()
            self.log_progress("✅ Publication completed successfully!")
            
            # Create PRs if enabled and changes exist
            self.create_prs_if_needed()
            
        except Exception as e:
            self.log_progress(f"❌ Error: {str(e)}")
            raise


# --- add helpers ---
def _load_yaml_maybe(path):
    if not path:
        return {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                data = yaml.safe_load(f) or {}
                if not isinstance(data, dict):
                    logging.warning(f"{path} did not parse to a mapping; ignoring.")
                    return {}
                return data
            except Exception as e:
                logging.warning(f"Failed to parse YAML {path}: {e}")
                return {}
    return {}

def _deep_merge_dicts(base: dict, override: dict) -> dict:
    """Return a deep merge of two dicts without mutating inputs (override wins)."""
    result = deepcopy(base)
    for k, v in (override or {}).items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge_dicts(result[k], v)
        else:
            result[k] = deepcopy(v)
    return result

# --- replace your existing load_config() with this ---
def load_config(global_path=None, local_path="release-config.yaml"):
    """
    Load config by deep-merging:
      1) global defaults (from smart-html)
      2) local overrides (from caller repo)
    Local overrides win per key.
    """
    global_cfg = _load_yaml_maybe(global_path)
    local_cfg = _load_yaml_maybe(local_path)
    merged = _deep_merge_dicts(global_cfg, local_cfg)
    return merged


def save_config(config):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        yaml.safe_dump(config, f, default_flow_style=False)

if tk:
    class CustomCheckbox(tk.Canvas):
        """Custom checkbox widget that supports theming and proper sizing"""
        def __init__(self, parent, text="", variable=None, command=None, 
                     font=None, bg="#FFFFFF", fg="#000000", 
                     check_color="#6C63FF", size=20):
            super().__init__(parent, highlightthickness=0, bg=bg)
            
            self.variable = variable or tk.BooleanVar()
            self.command = command
            self.text = text
            self.font = font
            self.bg = bg
            self.fg = fg
            self.check_color = check_color
            self.size = size
            self.checkbox_id = None
            self.check_id = None
            self.text_id = None
            
            self.setup_widget()
            self.bind("<Button-1>", self.toggle)
            self.bind("<Enter>", self.on_enter)
            self.bind("<Leave>", self.on_leave)
            
        def setup_widget(self):
            """Setup the checkbox widget"""
            # Calculate dimensions
            padding = 5
            box_size = self.size
            
            # Create checkbox square
            self.checkbox_id = self.create_rectangle(
                padding, padding, 
                padding + box_size, padding + box_size,
                outline=self.fg, width=2, fill=self.bg
            )
            
            # Create checkmark (initially hidden)
            check_padding = box_size * 0.25
            self.check_id = self.create_line(
                padding + check_padding, 
                padding + box_size/2,
                padding + box_size/2.5, 
                padding + box_size - check_padding,
                padding + box_size - check_padding, 
                padding + check_padding,
                width=3, fill=self.check_color, 
                state='hidden'
            )
            
            # Create text label
            if self.text:
                self.text_id = self.create_text(
                    padding * 2 + box_size, 
                    padding + box_size/2,
                    text=self.text, font=self.font,
                    fill=self.fg, anchor='w'
                )
                
                # Adjust canvas size to fit content
                bbox = self.bbox('all')
                if bbox:
                    self.configure(width=bbox[2] + padding, 
                                 height=bbox[3] + padding)
            else:
                self.configure(width=box_size + padding * 2,
                             height=box_size + padding * 2)
            
            # Update visual state
            self.update_visual()
            
        def toggle(self, event=None):
            """Toggle checkbox state"""
            self.variable.set(not self.variable.get())
            self.update_visual()
            if self.command:
                self.command()
                
        def update_visual(self):
            """Update checkbox visual state"""
            if self.variable.get():
                self.itemconfig(self.check_id, state='normal')
                self.itemconfig(self.checkbox_id, fill=self.bg)
            else:
                self.itemconfig(self.check_id, state='hidden')
                self.itemconfig(self.checkbox_id, fill=self.bg)
                
        def on_enter(self, event):
            """Mouse enter effect"""
            self.configure(cursor='hand2')
            # Subtle highlight effect
            if not self.variable.get():
                self.itemconfig(self.checkbox_id, fill='#F0F0F0' if self.bg == '#FFFFFF' else '#3A4356')
            
        def on_leave(self, event):
            """Mouse leave effect"""
            self.configure(cursor='')
            self.update_visual()
            
        def update_colors(self, bg, fg, check_color):
            """Update colors for theme changes"""
            self.bg = bg
            self.fg = fg
            self.check_color = check_color
            self.configure(bg=bg)
            self.itemconfig(self.checkbox_id, outline=fg, fill=bg)
            self.itemconfig(self.check_id, fill=check_color)
            if self.text_id:
                self.itemconfig(self.text_id, fill=fg)
            self.update_visual()

    class ModernFHIRPublisherGUI:
        def __init__(self):
            self.root = tk.Tk()
            self.root.title("FHIR IG Publisher")
            
            # Enable high DPI support FIRST, before any other setup
            self.setup_high_dpi_support()
            
            # Much bigger default window size
            self.root.geometry("1200x900")
            self.root.minsize(1200, 900)
            
            # Initialize theme
            self.is_dark_theme = False
            self.setup_colors()
            self.setup_fonts()
            self.setup_styles()
            
            # Load configuration
            # For local runs, there probably is no global config; this call handles None gracefully
            config = load_config(global_path=os.environ.get("GLOBAL_RELEASE_CONFIG"), local_path="release-config.yaml")

            self.setup_variables(config)
            
            # Store custom checkboxes for theme updates
            self.custom_checkboxes = []
            
            # Create the interface
            self.create_interface()
            
            # Center window
            self.center_window()
            
            # Apply initial theme
            self.apply_theme()
        
        def setup_high_dpi_support(self):
            """Enable high DPI support for crisp rendering on high resolution screens"""
            try:
                # Try to enable DPI awareness on Windows
                import ctypes
                
                # Tell Windows this app is DPI aware
                try:
                    ctypes.windll.shcore.SetProcessDpiAwareness(1)  # Per-monitor DPI aware
                except:
                    try:
                        ctypes.windll.user32.SetProcessDPIAware()  # System DPI aware (fallback)
                    except:
                        pass
                
                # Get system DPI scaling
                try:
                    # Get DPI of primary monitor
                    hdc = ctypes.windll.user32.GetDC(0)
                    dpi_x = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
                    ctypes.windll.user32.ReleaseDC(0, hdc)
                    
                    # Calculate scale factor (96 DPI is 100% scaling)
                    scale_factor = dpi_x / 96.0
                    
                    # Apply scaling to tkinter
                    self.root.tk.call('tk', 'scaling', scale_factor)
                    
                    # Store scale factor for font sizing
                    self.dpi_scale = scale_factor
                    print(f"High DPI support enabled: {dpi_x} DPI, scale factor: {scale_factor:.2f}")
                    
                except:
                    # Fallback: detect from tkinter
                    dpi = self.root.winfo_fpixels('1i')
                    scale_factor = dpi / 72.0  # 72 is default tkinter DPI
                    self.root.tk.call('tk', 'scaling', scale_factor)
                    self.dpi_scale = scale_factor
                    print(f"High DPI support enabled (fallback): {dpi:.1f} DPI, scale factor: {scale_factor:.2f}")
                    
            except ImportError:
                # Non-Windows or no ctypes available
                try:
                    # Try to get DPI info from tkinter
                    dpi = self.root.winfo_fpixels('1i')
                    if dpi > 96:  # If higher than standard DPI
                        scale_factor = dpi / 96.0
                        self.root.tk.call('tk', 'scaling', scale_factor)
                        self.dpi_scale = scale_factor
                        print(f"High DPI support enabled (cross-platform): {dpi:.1f} DPI, scale factor: {scale_factor:.2f}")
                    else:
                        self.dpi_scale = 1.0
                        print("Standard DPI detected, no scaling applied")
                except:
                    self.dpi_scale = 1.0
                    print("Using default DPI scaling")
            
        def setup_colors(self):
            """Define color schemes for light and dark themes"""
            self.colors = {
                'light': {
                    'bg_primary': '#F8F9FF',
                    'bg_secondary': '#FFFFFF', 
                    'bg_accent': '#E8E5FF',
                    'text_primary': '#2D3748',
                    'text_secondary': '#4A5568',
                    'text_muted': '#718096',
                    'accent': '#6C63FF',
                    'accent_hover': '#5A52D5',
                    'success': '#48BB78',
                    'warning': '#ED8936',
                    'error': '#E53E3E',
                    'border': '#E2E8F0',
                    'button_bg': '#6C63FF',
                    'button_hover': '#5A52D5',
                    'checkbox_check': '#6C63FF'
                },
                'dark': {
                    'bg_primary': '#1A202C',
                    'bg_secondary': '#2D3748',
                    'bg_accent': '#4A5568',
                    'text_primary': '#F7FAFC',
                    'text_secondary': '#E2E8F0',
                    'text_muted': '#A0AEC0',
                    'accent': '#90CDF4',
                    'accent_hover': '#63B3ED',
                    'success': '#68D391',
                    'warning': '#F6AD55',
                    'error': '#FC8181',
                    'border': '#4A5568',
                    'button_bg': '#4299E1',
                    'button_hover': '#3182CE',
                    'checkbox_check': '#90CDF4'
                }
            }
        
        def setup_fonts(self):
            """Setup custom fonts with conservative DPI scaling for crisp rendering"""
            try:
                # Much more conservative scaling - only scale moderately for very high DPI
                if hasattr(self, 'dpi_scale') and self.dpi_scale > 1.5:
                    # Only apply scaling for very high DPI displays, and keep it modest
                    base_scale = 1.0 + ((self.dpi_scale - 1.0) * 0.3)  # Much more conservative
                else:
                    base_scale = 1.0  # No scaling for normal displays
                
                # Ensure we don't go crazy with scaling
                base_scale = min(base_scale, 1.4)  # Cap at 40% increase maximum
                
                self.fonts = {
                    'heading': tkfont.Font(family="Segoe UI", size=int(20 * base_scale), weight="bold"),
                    'subheading': tkfont.Font(family="Segoe UI", size=int(14 * base_scale), weight="bold"),
                    'body': tkfont.Font(family="Segoe UI", size=int(10 * base_scale)),
                    'body_bold': tkfont.Font(family="Segoe UI", size=int(10 * base_scale), weight="bold"),
                    'small': tkfont.Font(family="Segoe UI", size=int(9 * base_scale)),
                    'button': tkfont.Font(family="Segoe UI", size=int(10 * base_scale), weight="bold"),
                    'button_large': tkfont.Font(family="Segoe UI", size=int(11 * base_scale), weight="bold"),
                    'code': tkfont.Font(family="Consolas", size=int(9 * base_scale))
                }
                
                # Conservative icon sizing
                self.icon_size = int(20 * base_scale)
                
                # Checkbox size
                self.checkbox_size = int(20 * base_scale)
                
                # Store conservative scale for UI elements
                self.ui_scale = base_scale
                
                print(f"Conservative scaling applied: {base_scale:.2f}x (from DPI scale {getattr(self, 'dpi_scale', 1.0):.2f})")
                
            except:
                # Fallback fonts - normal sizes
                self.fonts = {
                    'heading': tkfont.Font(size=20, weight="bold"),
                    'subheading': tkfont.Font(size=14, weight="bold"),
                    'body': tkfont.Font(size=10),
                    'body_bold': tkfont.Font(size=10, weight="bold"),
                    'small': tkfont.Font(size=9),
                    'button': tkfont.Font(size=10, weight="bold"),
                    'button_large': tkfont.Font(size=11, weight="bold"),
                    'code': tkfont.Font(family="Courier", size=9)
                }
                self.icon_size = 20
                self.checkbox_size = 20
                self.ui_scale = 1.0
        
        def setup_styles(self):
            """Setup ttk styles"""
            self.style = ttk.Style()
            
            # Configure notebook style
            self.style.configure('Modern.TNotebook', borderwidth=0, relief='flat')
            self.style.configure('Modern.TNotebook.Tab', 
                               padding=[20, 12], 
                               font=self.fonts['body_bold'])
            
        def get_current_colors(self):
            """Get current theme colors"""
            return self.colors['dark' if self.is_dark_theme else 'light']
        
        def apply_theme(self):
            """Apply current theme colors to all widgets"""
            colors = self.get_current_colors()
            
            # Root window
            self.root.configure(bg=colors['bg_primary'])
            
            # Update ttk styles
            self.style.configure('Modern.TNotebook', background=colors['bg_secondary'])
            self.style.configure('Modern.TNotebook.Tab',
                               background=colors['bg_accent'],
                               foreground=colors['text_secondary'],
                               lightcolor=colors['border'],
                               borderwidth=1)
            self.style.map('Modern.TNotebook.Tab',
                          background=[('selected', colors['bg_secondary'])],
                          foreground=[('selected', colors['accent'])])
            
            # Update custom checkboxes
            for checkbox in self.custom_checkboxes:
                checkbox.update_colors(colors['bg_accent'], colors['text_primary'], colors['checkbox_check'])
            
            # Update all frames
            for widget in self.root.winfo_children():
                self.update_widget_colors(widget, colors)
        
        def update_widget_colors(self, widget, colors):
            """Recursively update widget colors"""
            widget_class = widget.winfo_class()
            
            if widget_class == 'Frame':
                widget.configure(bg=colors['bg_secondary'])
            elif widget_class == 'Label':
                widget.configure(bg=colors['bg_secondary'], fg=colors['text_primary'])
            elif widget_class == 'Entry':
                widget.configure(bg=colors['bg_primary'], fg=colors['text_primary'],
                               insertbackground=colors['text_primary'])
            elif widget_class == 'Button':
                if hasattr(widget, 'is_primary') and widget.is_primary:
                    widget.configure(bg=colors['button_bg'], fg='white',
                                   activebackground=colors['button_hover'])
                else:
                    widget.configure(bg=colors['bg_accent'], fg=colors['text_primary'],
                                   activebackground=colors['border'])
            elif widget_class == 'Text':
                widget.configure(bg=colors['bg_primary'], fg=colors['text_primary'],
                               insertbackground=colors['text_primary'])
            
            # Recursively update children
            for child in widget.winfo_children():
                self.update_widget_colors(child, colors)
        
        def toggle_theme(self):
            """Toggle between light and dark theme"""
            self.is_dark_theme = not self.is_dark_theme
            self.theme_button.configure(text="☀️" if self.is_dark_theme else "🌙")
            self.apply_theme()
        
        def setup_variables(self, config):
            """Initialize tkinter variables with config values"""
            self.source_repo = tk.StringVar(value=config.get('source_repo', 'https://github.com/WorldHealthOrganization/smart-dak-pnc'))
            self.source_branch = tk.StringVar(value=config.get('source_branch', 'v0.9.9_releaseCandidate'))
            self.source_dir = tk.StringVar(value=config.get('source_dir', ''))
            self.history_repo = tk.StringVar(value=config.get('history_repo', 'https://github.com/HL7/fhir-ig-history-template'))
            self.history_branch = tk.StringVar(value=config.get('history_branch', 'main'))
            self.webroot_repo = tk.StringVar(value=config.get('webroot_repo', 'https://github.com/WorldHealthOrganization/smart-html'))
            self.webroot_branch = tk.StringVar(value=config.get('webroot_branch', 'main'))
            self.enable_sparse_checkout = tk.BooleanVar(value=config.get('enable_sparse_checkout', False))
            self.sparse_dirs = tk.StringVar(value=' '.join(config.get('sparse_dirs', ['templates', 'assets'])))
            
            # GitHub PR settings
            self.enable_pr_creation = tk.BooleanVar(value=config.get('enable_pr_creation', False))
            self.github_token = tk.StringVar(value=config.get('github_token', ''))
            self.webroot_pr_target_branch = tk.StringVar(value=config.get('webroot_pr_target_branch', 'main'))
            self.registry_pr_target_branch = tk.StringVar(value=config.get('registry_pr_target_branch', 'master'))
        
        def center_window(self):
            """Center the window on screen and ensure it fits"""
            self.root.update_idletasks()
            width = self.root.winfo_width()
            height = self.root.winfo_height()
            
            # Get screen dimensions
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            
            # Calculate position (centered)
            x = max(0, (screen_width // 2) - (width // 2))
            y = max(0, (screen_height // 2) - (height // 2))
            
            # Ensure window fits on screen
            if width > screen_width:
                width = screen_width
                x = 0
            if height > screen_height:
                height = screen_height
                y = 0
                
            self.root.geometry(f'{width}x{height}+{x}+{y}')

        def create_interface(self):
            """Create the main interface"""
            colors = self.get_current_colors()
            
            # Main container
            main_frame = tk.Frame(self.root, bg=colors['bg_primary'], padx=20, pady=20)
            main_frame.pack(fill=tk.BOTH, expand=True)
            
            # Header section
            self.create_header(main_frame)
            
            # Content notebook - DON'T let it expand infinitely
            self.create_notebook(main_frame)
            
            # Action buttons - ALWAYS visible at bottom
            self.create_action_buttons(main_frame)
            
            # Progress section
            self.create_progress_section(main_frame)
        
        def create_header(self, parent):
            """Create the compact header section with aligned title and theme toggle"""
            colors = self.get_current_colors()
            
            header_frame = tk.Frame(parent, bg=colors['bg_secondary'], 
                                  relief=tk.FLAT, bd=0)
            header_frame.pack(fill=tk.X, pady=(0, 10))
            
            # Single horizontal container for title and theme toggle
            title_frame = tk.Frame(header_frame, bg=colors['bg_secondary'])
            title_frame.pack(fill=tk.X, padx=20, pady=15)
            
            # Left side - Icon and title text
            left_content = tk.Frame(title_frame, bg=colors['bg_secondary'])
            left_content.pack(side=tk.LEFT, anchor=tk.W)
            
            # Horizontal layout for icon and title text
            title_container = tk.Frame(left_content, bg=colors['bg_secondary'])
            title_container.pack(anchor=tk.W)
            
            # Icon
            icon_label = tk.Label(title_container, text="🧬", 
                                font=('Segoe UI', self.icon_size),
                                bg=colors['bg_secondary'])
            icon_label.pack(side=tk.LEFT, padx=(0, 10))
            
            # Title and subtitle container
            text_container = tk.Frame(title_container, bg=colors['bg_secondary'])
            text_container.pack(side=tk.LEFT, anchor=tk.W)
            
            # Title
            title_label = tk.Label(text_container, text="FHIR IG Publisher", 
                                 font=self.fonts['subheading'],
                                 bg=colors['bg_secondary'], 
                                 fg=colors['accent'])
            title_label.pack(anchor=tk.W)
            
            # Subtitle
            subtitle_label = tk.Label(text_container, 
                                    text="Configure and publish FHIR Implementation Guides",
                                    font=self.fonts['small'],
                                    bg=colors['bg_secondary'], 
                                    fg=colors['text_muted'])
            subtitle_label.pack(anchor=tk.W)
            
            # Right side - Theme toggle button (aligned with title)
            theme_font_size = int(12 * getattr(self, 'ui_scale', 1.0))
            theme_padding = max(6, int(8 * getattr(self, 'ui_scale', 1.0)))
            
            self.theme_button = tk.Button(title_frame, text="🌙", 
                                        command=self.toggle_theme,
                                        font=('Segoe UI', theme_font_size), 
                                        relief=tk.FLAT,
                                        bg=colors['bg_accent'],
                                        fg=colors['text_primary'],
                                        padx=theme_padding, 
                                        pady=theme_padding//2)
            self.theme_button.pack(side=tk.RIGHT, anchor=tk.E)
        
        def create_notebook(self, parent):
            """Create the tabbed notebook interface with proper space distribution"""
            colors = self.get_current_colors()
            
            # Create a frame for the notebook that can expand properly
            notebook_frame = tk.Frame(parent, bg=colors['bg_primary'])
            notebook_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
            
            # Notebook without fixed height - let it expand naturally
            self.notebook = ttk.Notebook(notebook_frame, style='Modern.TNotebook')
            self.notebook.pack(fill=tk.BOTH, expand=True)
            
            # Source tab with better spacing
            source_frame = tk.Frame(self.notebook, bg=colors['bg_secondary'])
            self.notebook.add(source_frame, text="📂  Source Configuration")
            self.create_source_tab(source_frame)
            
            # Repository tab with better spacing
            repo_frame = tk.Frame(self.notebook, bg=colors['bg_secondary'])
            self.notebook.add(repo_frame, text="🔗  Repository Settings")
            self.create_repository_tab(repo_frame)
            
            # Advanced tab with better spacing
            advanced_frame = tk.Frame(self.notebook, bg=colors['bg_secondary'])
            self.notebook.add(advanced_frame, text="⚡  Advanced Options")
            self.create_advanced_tab(advanced_frame)
            
            # NEW: GitHub PR tab
            github_frame = tk.Frame(self.notebook, bg=colors['bg_secondary'])
            self.notebook.add(github_frame, text="🔀  Pull Requests")
            self.create_github_tab(github_frame)
        
        def create_field(self, parent, label_text, description, variable, icon=""):
            """Create a well-spaced modern field with label, description, and entry"""
            colors = self.get_current_colors()
            
            field_frame = tk.Frame(parent, bg=colors['bg_secondary'])
            field_frame.pack(fill=tk.X, expand=True, pady=15, padx=20)
            
            # Label with icon
            label_frame = tk.Frame(field_frame, bg=colors['bg_secondary'])
            label_frame.pack(fill=tk.X)
            
            label = tk.Label(label_frame, text=f"{icon} {label_text}" if icon else label_text,
                           font=self.fonts['body_bold'], 
                           bg=colors['bg_secondary'], fg=colors['text_primary'])
            label.pack(anchor=tk.W)
            
            # Description with better spacing
            desc_label = tk.Label(field_frame, text=description,
                                font=self.fonts['small'], 
                                bg=colors['bg_secondary'], fg=colors['text_muted'])
            desc_label.pack(anchor=tk.W, pady=(3, 10))  # Better spacing
            
            # Entry field with better height
            entry = tk.Entry(field_frame, textvariable=variable, 
                           font=self.fonts['body'],
                           bg=colors['bg_primary'], fg=colors['text_primary'],
                           insertbackground=colors['text_primary'],
                           relief=tk.FLAT, bd=0, highlightthickness=2,
                           highlightcolor=colors['accent'],
                           highlightbackground=colors['border'])
            entry.pack(fill=tk.X, expand=True, ipady=8)  # Better height
            
            return entry
        
        def create_password_field(self, parent, label_text, description, variable, icon=""):
            """Create a password field (shows asterisks)"""
            colors = self.get_current_colors()
            
            field_frame = tk.Frame(parent, bg=colors['bg_secondary'])
            field_frame.pack(fill=tk.X, expand=True, pady=15, padx=20)
            
            # Label with icon
            label_frame = tk.Frame(field_frame, bg=colors['bg_secondary'])
            label_frame.pack(fill=tk.X)
            
            label = tk.Label(label_frame, text=f"{icon} {label_text}" if icon else label_text,
                           font=self.fonts['body_bold'], 
                           bg=colors['bg_secondary'], fg=colors['text_primary'])
            label.pack(anchor=tk.W)
            
            # Description with better spacing
            desc_label = tk.Label(field_frame, text=description,
                                font=self.fonts['small'], 
                                bg=colors['bg_secondary'], fg=colors['text_muted'])
            desc_label.pack(anchor=tk.W, pady=(3, 10))
            
            # Password entry field
            entry = tk.Entry(field_frame, textvariable=variable, show="*",
                           font=self.fonts['body'],
                           bg=colors['bg_primary'], fg=colors['text_primary'],
                           insertbackground=colors['text_primary'],
                           relief=tk.FLAT, bd=0, highlightthickness=2,
                           highlightcolor=colors['accent'],
                           highlightbackground=colors['border'])
            entry.pack(fill=tk.X, expand=True, ipady=8)
            
            return entry
        
        def create_source_tab(self, parent):
            """Create source configuration tab with proper spacing"""
            colors = self.get_current_colors()

            # Container for canvas and scrollbar
            scrollable_container = tk.Frame(parent, bg=colors['bg_secondary'])
            scrollable_container.pack(side="top", fill="both", expand=True)

            # Canvas and scrollbar
            canvas = tk.Canvas(scrollable_container, bg=colors['bg_secondary'], highlightthickness=0)
            scrollbar = ttk.Scrollbar(scrollable_container, orient="vertical", command=canvas.yview)
            scrollbar.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)

            scrollable_frame = tk.Frame(canvas, bg=colors['bg_secondary'])
            scrollable_frame.pack_propagate(False)

            window_id = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

            def update_scrollregion(event):
                canvas.configure(scrollregion=canvas.bbox("all"))
            scrollable_frame.bind("<Configure>", update_scrollregion)

            # Optional: match width of scrollable frame to canvas
            def resize_scrollable(event):
                canvas.itemconfig(window_id, width=event.width, height=event.height)
            canvas.bind("<Configure>", resize_scrollable)

            # Mouse wheel scroll
            def _on_mousewheel(event):
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
            canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

            # --- Your content inside scrollable_frame ---
            title_label = tk.Label(scrollable_frame, text="Source Configuration",
                                font=self.fonts['subheading'],
                                bg=colors['bg_secondary'], fg=colors['text_primary'])
            title_label.pack(anchor=tk.W, pady=(20, 20), padx=20)

            self.create_field(scrollable_frame, "Source Repository URL", 
                            "Git repository containing your FHIR IG source files",
                            self.source_repo, "🌐")

            self.create_field(scrollable_frame, "Source Branch", 
                            "Specific branch or tag to use from the source repository",
                            self.source_branch, "🔀")

            self.create_directory_field(scrollable_frame)
                
        def create_directory_field(self, parent):
            """Create directory field with browse button and proper spacing"""
            colors = self.get_current_colors()
            
            field_frame = tk.Frame(parent, bg=colors['bg_secondary'])
            field_frame.pack(fill=tk.X, pady=15, padx=20)  # Better spacing
            
            # Label
            label = tk.Label(field_frame, text="📁 Local Source Directory (Optional)",
                           font=self.fonts['body_bold'], 
                           bg=colors['bg_secondary'], fg=colors['text_primary'])
            label.pack(anchor=tk.W)
            
            # Description with better spacing
            desc_label = tk.Label(field_frame, text="Use existing local directory instead of cloning from repository",
                                font=self.fonts['small'], 
                                bg=colors['bg_secondary'], fg=colors['text_muted'])
            desc_label.pack(anchor=tk.W, pady=(5, 10))  # Better spacing
            
            # Entry and button frame
            entry_frame = tk.Frame(field_frame, bg=colors['bg_secondary'])
            entry_frame.pack(fill=tk.X)
            
            # Entry with better height
            self.source_dir_entry = tk.Entry(entry_frame, textvariable=self.source_dir,
                                           font=self.fonts['body'],
                                           bg=colors['bg_primary'], fg=colors['text_primary'],
                                           insertbackground=colors['text_primary'],
                                           relief=tk.FLAT, bd=0, highlightthickness=2,
                                           highlightcolor=colors['accent'],
                                           highlightbackground=colors['border'])
            self.source_dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, 
                                     ipady=8)  # Better height
            
            # Browse button
            browse_btn = tk.Button(entry_frame, text="🔍 Browse", 
                                 command=self.browse_source_directory,
                                 font=self.fonts['button'],
                                 bg=colors['bg_accent'], fg=colors['text_primary'],
                                 relief=tk.FLAT, 
                                 padx=15, pady=8)  # Better padding
            browse_btn.pack(side=tk.RIGHT, padx=(15, 0))  # Better spacing

        def create_repository_tab(self, parent):
            """Create repository configuration tab with proper spacing"""
            colors = self.get_current_colors()

            # Container for canvas + scrollbar
            scrollable_container = tk.Frame(parent, bg=colors['bg_secondary'])
            scrollable_container.pack(side="top", fill="both", expand=True)

            # Canvas + scrollbar
            canvas = tk.Canvas(scrollable_container, bg=colors['bg_secondary'], highlightthickness=0)
            scrollbar = ttk.Scrollbar(scrollable_container, orient="vertical", command=canvas.yview)
            canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

            scrollable_frame = tk.Frame(canvas, bg=colors['bg_secondary'])
            window_id = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

            # Update scrollregion when content changes
            def update_scrollregion(event):
                canvas.configure(scrollregion=canvas.bbox("all"))
            scrollable_frame.bind("<Configure>", update_scrollregion)

            # Match scrollable_frame width to canvas width
            def resize_scrollable(event):
                canvas.itemconfig(window_id, width=event.width)
            canvas.bind("<Configure>", resize_scrollable)

            # Optional: mousewheel scrolling
            def _on_mousewheel(event):
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
            canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

            # ---- Content ----
            title_label = tk.Label(scrollable_frame, text="Repository Configuration",
                                font=self.fonts['subheading'],
                                bg=colors['bg_secondary'], fg=colors['text_primary'])
            title_label.pack(anchor=tk.W, pady=(20, 20), padx=20)

            self.create_field(scrollable_frame, "History Repository URL",
                            "Repository containing the IG history template for version management",
                            self.history_repo, "📚")

            self.create_field(scrollable_frame, "History Branch",
                            "Branch to use from the history repository",
                            self.history_branch, "🌿")

            self.create_field(scrollable_frame, "Webroot Repository URL",
                            "Repository containing web publishing templates and assets",
                            self.webroot_repo, "🌍")

            self.create_field(scrollable_frame, "Webroot Branch",
                            "Branch to use from the webroot repository", 
                            self.webroot_branch, "🌳")

            spacer = tk.Frame(scrollable_frame, height=1, bg=colors['bg_secondary'])
            spacer.pack(fill='both', expand=True)

        def create_advanced_tab(self, parent):
            """Create advanced options tab with custom checkbox"""
            colors = self.get_current_colors()
            
            title_label = tk.Label(parent, text="Advanced Configuration",
                                 font=self.fonts['subheading'],
                                 bg=colors['bg_secondary'], fg=colors['text_primary'])
            title_label.pack(anchor=tk.W, pady=(0, 10))  # Reduced padding
            
            # Sparse checkout section - more compact
            sparse_frame = tk.Frame(parent, bg=colors['bg_accent'], relief=tk.FLAT, bd=1)
            sparse_frame.pack(fill=tk.X, pady=10)  # Reduced padding
            
            sparse_content = tk.Frame(sparse_frame, bg=colors['bg_accent'])
            sparse_content.pack(fill=tk.BOTH, padx=15, pady=15)  # Reduced padding
            
            # Section title
            sparse_title = tk.Label(sparse_content, text="⚡ Sparse Checkout Optimization",
                                  font=self.fonts['body_bold'],
                                  bg=colors['bg_accent'], fg=colors['accent'])
            sparse_title.pack(anchor=tk.W)
            
            # Description
            sparse_desc = tk.Label(sparse_content, 
                                 text="Optimize clone performance by downloading only specific folders",
                                 font=self.fonts['small'],
                                 bg=colors['bg_accent'], fg=colors['text_muted'])
            sparse_desc.pack(anchor=tk.W, pady=(1, 10))  # Reduced padding
            
            # Custom checkbox
            self.sparse_checkbox = CustomCheckbox(
                sparse_content,
                text="Enable sparse checkout for webroot repository",
                variable=self.enable_sparse_checkout,
                command=self.toggle_sparse_fields,
                font=self.fonts['body_bold'],
                bg=colors['bg_accent'],
                fg=colors['text_primary'],
                check_color=colors['checkbox_check'],
                size=self.checkbox_size
            )
            self.sparse_checkbox.pack(anchor=tk.W, pady=(0, 10))
            self.custom_checkboxes.append(self.sparse_checkbox)
            
            # Sparse directories field
            self.sparse_dir_frame = tk.Frame(sparse_content, bg=colors['bg_accent'])
            self.sparse_dir_frame.pack(fill=tk.X)
            
            sparse_label = tk.Label(self.sparse_dir_frame, text="📁 Folders to Download",
                                  font=self.fonts['body_bold'],
                                  bg=colors['bg_accent'], fg=colors['text_primary'])
            sparse_label.pack(anchor=tk.W)
            
            sparse_desc2 = tk.Label(self.sparse_dir_frame, 
                                  text="Space-separated list of directories to include in the clone",
                                  font=self.fonts['small'],
                                  bg=colors['bg_accent'], fg=colors['text_muted'])
            sparse_desc2.pack(anchor=tk.W, pady=(1, 5))  # Reduced padding
            
            self.sparse_entry = tk.Entry(self.sparse_dir_frame, textvariable=self.sparse_dirs,
                                       font=self.fonts['code'],
                                       bg=colors['bg_primary'], fg=colors['text_primary'],
                                       insertbackground=colors['text_primary'],
                                       relief=tk.FLAT, bd=0, highlightthickness=2,
                                       highlightcolor=colors['accent'],
                                       highlightbackground=colors['border'])
            self.sparse_entry.pack(fill=tk.X, ipady=5)  # Reasonable padding
            
            # Example - more compact
            example_frame = tk.Frame(self.sparse_dir_frame, bg=colors['bg_accent'])
            example_frame.pack(fill=tk.X, pady=(5, 15))  # Reduced padding
            
            example_label = tk.Label(example_frame, text="💡 Example:",
                                   font=self.fonts['small'],
                                   bg=colors['bg_accent'], fg=colors['warning'])
            example_label.pack(side=tk.LEFT)
            
            example_text = tk.Label(example_frame, text="templates assets css js images",
                                  font=self.fonts['code'],
                                  bg=colors['bg_accent'], fg=colors['text_muted'])
            example_text.pack(side=tk.LEFT, padx=(5, 0))
            
            # Initialize sparse field state
            self.toggle_sparse_fields()

        def create_github_tab(self, parent):
            """Create GitHub PR configuration tab with custom checkbox"""
            colors = self.get_current_colors()

            # Container for canvas + scrollbar
            scrollable_container = tk.Frame(parent, bg=colors['bg_secondary'])
            scrollable_container.pack(side="top", fill="both", expand=True)

            # Canvas + scrollbar
            canvas = tk.Canvas(scrollable_container, bg=colors['bg_secondary'], highlightthickness=0)
            scrollbar = ttk.Scrollbar(scrollable_container, orient="vertical", command=canvas.yview)
            canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

            scrollable_frame = tk.Frame(canvas, bg=colors['bg_secondary'])
            window_id = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

            def update_scrollregion(event):
                canvas.configure(scrollregion=canvas.bbox("all"))
            scrollable_frame.bind("<Configure>", update_scrollregion)

            def resize_scrollable(event):
                canvas.itemconfig(window_id, width=event.width)
            canvas.bind("<Configure>", resize_scrollable)

            def _on_mousewheel(event):
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
            canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

            # Content
            title_label = tk.Label(scrollable_frame, text="Pull Request Configuration",
                                font=self.fonts['subheading'],
                                bg=colors['bg_secondary'], fg=colors['text_primary'])
            title_label.pack(anchor=tk.W, pady=(20, 20), padx=20)

            # PR Enable section
            pr_frame = tk.Frame(scrollable_frame, bg=colors['bg_accent'], relief=tk.FLAT, bd=1)
            pr_frame.pack(fill=tk.X, pady=15, padx=20)

            pr_content = tk.Frame(pr_frame, bg=colors['bg_accent'])
            pr_content.pack(fill=tk.BOTH, padx=20, pady=20)

            # Section title
            pr_title = tk.Label(pr_content, text="🔀 Automatic Pull Request Creation",
                              font=self.fonts['body_bold'],
                              bg=colors['bg_accent'], fg=colors['accent'])
            pr_title.pack(anchor=tk.W)

            # Description
            pr_desc = tk.Label(pr_content, 
                             text="Automatically create pull requests after successful build",
                             font=self.fonts['small'],
                             bg=colors['bg_accent'], fg=colors['text_muted'])
            pr_desc.pack(anchor=tk.W, pady=(5, 15))

            # Custom checkbox for PR
            self.pr_checkbox = CustomCheckbox(
                pr_content,
                text="Enable automatic PR creation",
                variable=self.enable_pr_creation,
                command=self.toggle_pr_fields,
                font=self.fonts['body_bold'],
                bg=colors['bg_accent'],
                fg=colors['text_primary'],
                check_color=colors['checkbox_check'],
                size=self.checkbox_size
            )
            self.pr_checkbox.pack(anchor=tk.W, pady=(0, 15))
            self.custom_checkboxes.append(self.pr_checkbox)

            # PR fields frame
            self.pr_fields_frame = tk.Frame(pr_content, bg=colors['bg_accent'])
            self.pr_fields_frame.pack(fill=tk.X)

            # Create PR configuration fields
            self.create_password_field(scrollable_frame, "GitHub Personal Access Token (Optional)",
                                     "Token for external repos. GitHub Actions provides automatic auth for same repo",
                                     self.github_token, "🔑")

            self.create_field(scrollable_frame, "Webroot PR Target Branch",
                            "Target branch for webroot repository pull requests",
                            self.webroot_pr_target_branch, "🎯")

            self.create_field(scrollable_frame, "Registry PR Target Branch", 
                            "Target branch for IG registry pull requests",
                            self.registry_pr_target_branch, "🎯")

            # GitHub token help with updated info
            help_frame = tk.Frame(scrollable_frame, bg=colors['bg_accent'], relief=tk.FLAT, bd=1)
            help_frame.pack(fill=tk.X, pady=15, padx=20)
            
            help_content = tk.Frame(help_frame, bg=colors['bg_accent'])
            help_content.pack(fill=tk.BOTH, padx=20, pady=15)
            
            help_title = tk.Label(help_content, text="ℹ️ GitHub Authentication",
                                font=self.fonts['body_bold'],
                                bg=colors['bg_accent'], fg=colors['warning'])
            help_title.pack(anchor=tk.W)
            
            help_text = tk.Label(help_content,
                               text="""GitHub Actions:
• No token needed for same repository - uses GITHUB_TOKEN automatically
• For external repos, add PAT as secret: Settings → Secrets → Actions → New secret

Manual/Local runs:
1. Go to GitHub.com → Settings → Developer settings → Personal access tokens
2. Generate new token with 'repo' scope
3. Paste token above or set as GH_PAT environment variable""",
                               font=self.fonts['small'],
                               bg=colors['bg_accent'], fg=colors['text_muted'],
                               justify=tk.LEFT)
            help_text.pack(anchor=tk.W, pady=(5, 0))

            # Initialize PR field state
            self.toggle_pr_fields()

            spacer = tk.Frame(scrollable_frame, height=1, bg=colors['bg_secondary'])
            spacer.pack(fill='both', expand=True)
        
        def create_action_buttons(self, parent):
            """Create prominent action buttons with proper spacing"""
            colors = self.get_current_colors()
            
            # Make button frame more prominent with proper spacing
            button_frame = tk.Frame(parent, bg=colors['button_bg'], relief=tk.RAISED, bd=3)
            button_frame.pack(fill=tk.X, pady=15, padx=5)  # Normal packing, not forced to bottom
            
            # Add some padding inside the frame
            button_inner = tk.Frame(button_frame, bg=colors['button_bg'])
            button_inner.pack(fill=tk.X, padx=25, pady=20)  # Better padding
            
            # Create a centered container for buttons
            button_container = tk.Frame(button_inner, bg=colors['button_bg'])
            button_container.pack(anchor=tk.CENTER)
            
            # Save button with better sizing
            save_btn = tk.Button(button_container, text="💾 SAVE CONFIG",
                               command=self.save_configuration,
                               font=self.fonts['button'],
                               bg='#FF9800', fg='white',
                               relief=tk.RAISED, bd=2, 
                               padx=20, pady=12,  # Good padding
                               cursor='hand2')
            save_btn.pack(side=tk.LEFT, padx=(0, 20))  # Better spacing
            
            # Run button with better sizing
            self.run_btn = tk.Button(button_container, text="🚀 RUN PUBLISHER",
                                   command=self.run_publisher_threaded,
                                   font=self.fonts['button_large'],
                                   bg='#4CAF50', fg='white',
                                   relief=tk.RAISED, bd=3, 
                                   padx=30, pady=15,  # Good padding
                                   cursor='hand2',
                                   activebackground='#45a049')
            self.run_btn.is_primary = True
            self.run_btn.pack(side=tk.LEFT)
            
            # Add a label with better spacing
            info_label = tk.Label(button_inner, text="👆 Click RUN PUBLISHER to start processing",
                                font=self.fonts['small'],
                                bg=colors['button_bg'], fg='white')
            info_label.pack(pady=(15, 0))  # Better spacing
        
        def create_progress_section(self, parent):
            """Create progress section"""
            colors = self.get_current_colors()
            
            self.progress_frame = tk.Frame(parent, bg=colors['bg_primary'])
            
            # Progress label
            self.progress_label = tk.Label(self.progress_frame, text="",
                                         font=self.fonts['body_bold'],
                                         bg=colors['bg_primary'], fg=colors['accent'])
            self.progress_label.pack(pady=(0, 10))
            
            # Progress text area
            self.progress_text = scrolledtext.ScrolledText(self.progress_frame,
                                                         height=12,
                                                         font=self.fonts['small'],
                                                         bg=colors['bg_secondary'],
                                                         fg=colors['text_primary'],
                                                         insertbackground=colors['text_primary'],
                                                         relief=tk.FLAT, bd=0)
            self.progress_text.pack(fill=tk.BOTH, expand=True)
        
        def toggle_sparse_fields(self):
            """Toggle sparse directory fields based on checkbox state"""
            if self.enable_sparse_checkout.get():
                self.sparse_entry.configure(state='normal')
            else:
                self.sparse_entry.configure(state='disabled')

        def toggle_pr_fields(self):
            """Toggle PR configuration fields based on checkbox state"""
            # This function can be used to show/hide PR fields if needed
            # Currently all fields are always visible for simplicity
            pass
        
        def browse_source_directory(self):
            """Open directory browser for source directory"""
            directory = filedialog.askdirectory(title="Select FHIR IG Source Directory")
            if directory:
                self.source_dir.set(directory)
        
        def save_configuration(self):
            """Save current configuration to YAML file"""
            config = {
                'source_repo': self.source_repo.get(),
                'source_branch': self.source_branch.get(),
                'source_dir': self.source_dir.get(),
                'history_repo': self.history_repo.get(),
                'history_branch': self.history_branch.get(),
                'webroot_repo': self.webroot_repo.get(),
                'webroot_branch': self.webroot_branch.get(),
                'enable_sparse_checkout': self.enable_sparse_checkout.get(),
                'sparse_dirs': self.sparse_dirs.get().split() if self.sparse_dirs.get().strip() else [],
                # GitHub PR settings
                'enable_pr_creation': self.enable_pr_creation.get(),
                'github_token': self.github_token.get(),
                'webroot_pr_target_branch': self.webroot_pr_target_branch.get(),
                'registry_pr_target_branch': self.registry_pr_target_branch.get()
            }
            
            try:
                save_config(config)
                messagebox.showinfo("Success", "✅ Configuration saved successfully!")
            except Exception as e:
                messagebox.showerror("Error", f"❌ Failed to save configuration:\n{e}")
        
        def update_progress(self, message):
            """Update progress display"""
            self.progress_label.configure(text="🔄 Processing...")
            self.progress_text.insert(tk.END, f"{datetime.now().strftime('%H:%M:%S')} - {message}\n")
            self.progress_text.see(tk.END)
            self.root.update_idletasks()
        
        def show_progress(self):
            """Show progress section"""
            self.progress_frame.pack(fill=tk.BOTH, expand=True, pady=(20, 0))
            self.progress_text.delete(1.0, tk.END)
        
        def hide_progress(self):
            """Hide progress section"""
            self.progress_frame.pack_forget()
        
        def run_publisher_threaded(self):
            """Run publisher in separate thread"""
            def run_in_thread():
                try:
                    # Show progress
                    self.root.after(0, self.show_progress)
                    
                    # Prepare sparse directories
                    sparse_dirs = None
                    if self.enable_sparse_checkout.get() and self.sparse_dirs.get().strip():
                        sparse_dirs = self.sparse_dirs.get().split()
                    
                    # Create publisher instance
                    publisher = ReleasePublisher(
                        source_dir=self.source_dir.get() or None,
                        source_repo=self.source_repo.get() or None,
                        source_branch=self.source_branch.get() or None,
                        webroot_repo=self.webroot_repo.get() or None,
                        webroot_branch=self.webroot_branch.get() or None,
                        history_repo=self.history_repo.get() or None,
                        history_branch=self.history_branch.get() or None,
                        sparse_dirs=sparse_dirs,
                        enable_sparse_checkout=self.enable_sparse_checkout.get(),
                        progress_callback=lambda msg: self.root.after(0, lambda: self.update_progress(msg)),
                        # GitHub PR settings
                        github_token=self.github_token.get() or None,
                        enable_pr_creation=self.enable_pr_creation.get(),
                        webroot_pr_target_branch=self.webroot_pr_target_branch.get(),
                        registry_pr_target_branch=self.registry_pr_target_branch.get()
                    )
                    
                    # Run publisher
                    publisher.run()
                    
                    # Show success
                    self.root.after(0, lambda: self.progress_label.configure(text="✅ Completed!"))
                    self.root.after(0, lambda: messagebox.showinfo("Success", 
                                                                  "🎉 Publication completed successfully!\n\nYour FHIR IG has been published."))
                    
                except Exception as e:
                    # Show error
                    self.root.after(0, lambda: self.progress_label.configure(text="❌ Error occurred"))
                    self.root.after(0, lambda: self.update_progress(f"ERROR: {str(e)}"))
                    self.root.after(0, lambda: messagebox.showerror("Error", f"❌ An error occurred:\n\n{str(e)}"))
            
            # Start thread
            thread = threading.Thread(target=run_in_thread, daemon=True)
            thread.start()
        
        def run(self):
            """Start the GUI"""
            self.root.mainloop()

def main():
    parser = argparse.ArgumentParser(description="FHIR IG Publisher Release Utility")
    parser.add_argument('--gui', action='store_true', help='Launch beautiful GUI interface')
    parser.add_argument('--source', type=str, help='Path to the IG source folder')
    parser.add_argument('--source-repo', type=str, help='URL to the IG source repository')
    parser.add_argument('--source-branch', type=str, help='Branch name for IG source')
    parser.add_argument('--webroot-repo', type=str, help='Webroot repo URL')
    parser.add_argument('--webroot-branch', type=str, help='Webroot branch name')
    parser.add_argument('--history-repo', type=str, help='History repo URL')
    parser.add_argument('--history-branch', type=str, help='History branch name')
    parser.add_argument('--sparse', nargs='*', help='Sparse checkout folders for webroot')
    parser.add_argument('--enable-sparse', action='store_true', help='Enable sparse checkout')
    # GitHub PR arguments
    parser.add_argument('--enable-pr', action='store_true', help='Enable automatic PR creation')
    parser.add_argument('--github-token', type=str, help='GitHub personal access token')
    parser.add_argument('--webroot-pr-target', type=str, default='main', help='Webroot PR target branch')
    parser.add_argument('--registry-pr-target', type=str, default='master', help='Registry PR target branch')
    parser.add_argument('--global-config', type=str, help='Path to global default release-config.yaml')
    parser.add_argument('--local-config', type=str, default='release-config.yaml', help='Path to repo-specific release-config.yaml')

    args = parser.parse_args()

    # Check for GitHub Actions environment
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        print("🤖 Running in GitHub Actions environment")
        # Token will be auto-detected from GITHUB_TOKEN environment variable

    if args.gui:
        if not tk:
            print("❌ GUI not available: tkinter not found")
            sys.exit(1)
        gui = ModernFHIRPublisherGUI()
        gui.run()
    else:
        # Load merged config first (if you still read values from config elsewhere)
        config = load_config(global_path=args.global_config or os.environ.get("GLOBAL_RELEASE_CONFIG"),
                            local_path=args.local_config)

        # ... then proceed to use args or config as you already do ...
        publisher = ReleasePublisher(
            source_dir=args.source or config.get('source_dir'),
            source_repo=args.source_repo or config.get('source_repo'),
            source_branch=args.source_branch or config.get('source_branch'),
            webroot_repo=args.webroot_repo or config.get('webroot_repo'),
            webroot_branch=args.webroot_branch or config.get('webroot_branch'),
            history_repo=args.history_repo or config.get('history_repo'),
            history_branch=args.history_branch or config.get('history_branch'),
            sparse_dirs=args.sparse or config.get('sparse_dirs'),
            enable_sparse_checkout=args.enable_sparse or config.get('enable_sparse_checkout', False),
        )

        publisher.run()

if __name__ == '__main__':
    main()