#!/usr/bin/env python3
"""
Script to transform FHIR format HTML files (*.json.html, *.xml.html, *.ttl.html)
to use external file loading with copy buttons instead of inline code blocks.

The script:
1. Finds all *.{json,xml,ttl}.html files recursively
2. Extracts the resource type-id from the download link
3. Replaces the inline <pre> code block with a new structure that:
   - Adds a copy button
   - Loads content from the external file using data-src attribute
"""

import re
import os
from pathlib import Path
from typing import Tuple, Optional

def extract_type_id_and_format(html_content: str) -> Optional[Tuple[str, str]]:
    """
    Extract the type-id and format from the download link.
    Example: <a href="CodeSystem-KeyUsage-DEV.json" download>
    Or: <a download="null" href="CodeSystem-WHO.TRUST.ACTOR.json">
    Returns: ("CodeSystem-KeyUsage-DEV", "json")
    """
    # Pattern to match the download link (two variations)
    # Variation 1: <a href="..." download>
    pattern1 = r'<a href="([^"]+\.(json|xml|ttl))" download'
    match = re.search(pattern1, html_content)

    if not match:
        # Variation 2: <a download="null" href="...">
        pattern2 = r'<a download="[^"]*" href="([^"]+\.(json|xml|ttl))"'
        match = re.search(pattern2, html_content)

    if match:
        filename = match.group(1)
        format_ext = match.group(2)
        # Remove the extension to get type-id
        type_id = filename.replace(f'.{format_ext}', '')
        return type_id, format_ext

    return None

def transform_pre_block(html_content: str, type_id: str, format_type: str) -> str:
    """
    Replace the <pre> block with the new structure.

    Old structure:
      <pre class="json" data-fhir="generated" style="white-space: pre; text-wrap: nowrap; width: auto;">
        <code class="language-json" style="white-space: pre; text-wrap: nowrap;">{...}</code>
      </pre>

    New structure:
      <div style="position: relative;">
        <button class="btn-copy" style="position: absolute; top: 10px; right: 20px; z-index: 10;"
                title="Click to copy {format}"
                data-clipboard-target="#format-content"></button>
        <pre class="{format}" style="white-space: pre; overflow-x: auto;">
          <code id="format-content" class="language-{format}" data-src="{type-id}.{format}"></code>
        </pre>
      </div>
    """
    # TTL files use 'rdf' class and 'language-turtle' instead of 'ttl' and 'language-ttl'
    if format_type == 'ttl':
        pre_class = 'rdf'
        language_class = 'language-turtle'
    else:
        pre_class = format_type
        language_class = f'language-{format_type}'

    # Pattern to match the entire <pre> block with its content
    # For TTL, match 'rdf' class, for others match the format_type
    # Match both: <pre class="json" ...> and <pre ... class="json">
    # Use a more specific pattern: match <pre...><code...>...</code></pre>
    # This avoids accidentally matching content beyond the intended block
    pattern = r'<pre[^>]*class="' + pre_class + r'"[^>]*><code[^>]*>.*?</code></pre>'

    replacement = f'''<div style="position: relative;">
    <button class="btn-copy" style="position: absolute; top: 10px; right: 20px; z-index: 10;"
            title="Click to copy {format_type}"
            data-clipboard-target="#format-content"></button>
    <pre class="{pre_class}" style="white-space: pre; overflow-x: auto;"><code id="format-content" class="{language_class}" data-src="{type_id}.{format_type}"></code></pre>
  </div>'''

    # Use DOTALL flag to match across newlines
    transformed = re.sub(pattern, replacement, html_content, flags=re.DOTALL)

    return transformed

def add_format_loader_script(html_content: str) -> str:
    """
    Add the format-loader.js script reference after clipboard-btn.js if not already present.
    """
    if 'format-loader.js' in html_content:
        return html_content

    # Find the clipboard-btn.js script tag and add format-loader.js after it
    # Try pattern 1: <script type="text/javascript" src="..."> </script>
    pattern1 = r'(<script type="text/javascript" src="assets/js/clipboard-btn\.js"> </script>)'
    replacement1 = r'\1\n<script type="text/javascript" src="assets/js/format-loader.js"> </script>'

    result = re.sub(pattern1, replacement1, html_content)
    if result != html_content:
        return result

    # Try pattern 2: <script src="..." type="text/javascript"> </script>
    pattern2 = r'(<script src="assets/js/clipboard-btn\.js" type="text/javascript"> </script>)'
    replacement2 = r'\1\n  <script src="assets/js/format-loader.js" type="text/javascript"> </script>'

    return re.sub(pattern2, replacement2, html_content)

def process_file(file_path: Path) -> bool:
    """
    Process a single HTML file.
    Returns True if the file was modified, False otherwise.
    """
    try:
        # Read the file
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check if already transformed (has btn-copy button)
        already_transformed = 'btn-copy' in content
        has_format_loader = 'format-loader.js' in content

        if already_transformed and has_format_loader:
            print(f"  >> Skipping (already transformed): {file_path}")
            return False

        # Extract type-id and format
        result = extract_type_id_and_format(content)
        if not result:
            print(f"  !! Warning: Could not extract type-id from {file_path}")
            return False

        type_id, format_type = result

        transformed_content = content

        # Transform the content if not already done
        if not already_transformed:
            transformed_content = transform_pre_block(transformed_content, type_id, format_type)

        # Add format-loader.js script if not already present
        if not has_format_loader:
            transformed_content = add_format_loader_script(transformed_content)

        # Check if anything changed
        if transformed_content == content:
            print(f"  !! Warning: No changes made to {file_path}")
            return False

        # Write back to file
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(transformed_content)

        if already_transformed:
            print(f"  OK Added script: {file_path} (type-id: {type_id})")
        else:
            print(f"  OK Transformed: {file_path} (type-id: {type_id})")
        return True

    except Exception as e:
        print(f"  ERROR processing {file_path}: {e}")
        return False

def main():
    """Main function to process all format HTML files."""
    # Get the script directory
    script_dir = Path(__file__).parent

    print(f"Searching for format HTML files in: {script_dir}")
    print()

    # Find all format HTML files
    formats = ['json', 'xml', 'ttl']
    all_files = []

    for format_type in formats:
        pattern = f"**/*.{format_type}.html"
        files = list(script_dir.glob(pattern))
        all_files.extend(files)
        print(f"  Found {len(files)} *.{format_type}.html files")

    print(f"\nTotal files to process: {len(all_files)}")
    print()

    if not all_files:
        print("No files found to process!")
        return

    # Process each file
    modified_count = 0
    for file_path in sorted(all_files):
        if process_file(file_path):
            modified_count += 1

    print()
    print(f"Complete! Modified {modified_count} out of {len(all_files)} files")

if __name__ == '__main__':
    main()
