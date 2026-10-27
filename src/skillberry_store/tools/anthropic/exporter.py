# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0

"""Exporter for converting Skillberry skills to Anthropic skill format."""

import io
import logging
import zipfile
from typing import Dict, List, Optional, Any

import yaml

logger = logging.getLogger(__name__)


def extract_file_path_from_tags(tags: Optional[List[str]]) -> Optional[str]:
    """Extract file path from tags.

    Looks for tags in format "file:path/to/file.ext"

    Args:
        tags: List of tags

    Returns:
        File path or None
    """
    if not tags:
        return None

    for tag in tags:
        if tag.startswith("file:"):
            return tag[5:]  # Remove "file:" prefix

    return None


def normalize_file_path(file_path: str, skill_name: str) -> str:
    """Normalize file path by removing skill name prefix if present.

    Args:
        file_path: The file path
        skill_name: The skill name

    Returns:
        Normalized file path
    """
    # Remove leading skill name directory if present
    skill_prefix = f"{skill_name}/"
    if file_path.startswith(skill_prefix):
        return file_path[len(skill_prefix) :]
    return file_path


def render_frontmatter(fields: Dict[str, Any]) -> str:
    """Emit YAML frontmatter that round-trips through a real parser.

    Interpolating free-form text into ``key: value`` breaks on newlines and on
    ``: ``, and quietly truncates anything a YAML scanner reads as a comment or
    a quoted scalar. The consuming CLI parses frontmatter with a YAML library
    and drops any skill whose ``name``/``description`` is not a string, so an
    unescaped description is an uninstallable skill.
    See docs/design/npx.md §5.4.
    """
    body = yaml.safe_dump(
        fields,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        # Never line-wrap: the default 80-column folding rewrites a long
        # description with inserted newlines, which is legal YAML but silently
        # reflows the text an agent reads.
        width=10**6,
    )
    return f"---\n{body}---\n\n"


def skill_description(skill: Dict[str, Any]) -> str:
    """The description to publish for ``skill``, never empty.

    ``skill.get("description", default)`` returns ``None`` when the key is
    present with a ``None`` value, which is how a literal ``description: None``
    used to reach the emitted file. An absent, empty or ``None`` description
    resolves to a synthesized ``"Skill: <name>"`` instead: the consuming CLI
    requires a non-empty string and drops the skill otherwise (§1.3/§5.4).
    """
    description = skill.get("description")
    if isinstance(description, str) and description.strip():
        return description
    return f"Skill: {skill.get('name') or 'unnamed'}"


def generate_skill_md(
    skill: Dict[str, Any],
    has_file_structure: bool,
    snippets: List[Dict[str, Any]],
    name_override: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate SKILL.md content with frontmatter.

    Args:
        skill: The skill dictionary
        has_file_structure: Whether there's a file structure
        snippets: List of snippet dictionaries
        name_override: Value to emit as the frontmatter ``name`` instead of the
            stored SBS name. The well-known path passes the skill's slug, so
            the file an agent loads carries a name matching its own directory
            (docs/design/npx.md §5.8 #1). Existing callers pass nothing and
            keep emitting the raw SBS name.
        metadata: Extra frontmatter ``metadata`` mapping. The well-known path
            uses it to carry the original SBS name when the slug differs, and
            ``internal: true``, which the consuming CLI honours as a per-skill
            "do not offer this" flag (docs/design/npx.md §4.3).

    Returns:
        SKILL.md content
    """
    description = skill_description(skill)
    fields: Dict[str, Any] = {
        "name": name_override or skill["name"],
        "description": description,
    }
    if metadata:
        fields["metadata"] = dict(metadata)

    # Check if there's a LICENSE.txt file in snippets
    license_snippet = None
    for snippet in snippets:
        file_path = extract_file_path_from_tags(snippet.get("tags"))
        if file_path and "license" in file_path.lower():
            license_snippet = snippet
            break

    if license_snippet:
        fields["license"] = "Proprietary. LICENSE.txt has complete terms"

    content = render_frontmatter(fields)

    if not has_file_structure:
        content += f"# {skill['name']}\n\n"
        content += f"{description}\n\n"

    return content


def get_tool_language(tool: Dict[str, Any]) -> str:
    """Determine programming language from tool.

    Args:
        tool: The tool dictionary

    Returns:
        Language: 'python', 'bash', or 'other'
    """
    lang = tool.get("programming_language", "").lower()
    if lang in ("python", "py"):
        return "python"
    if lang in ("bash", "sh", "shell"):
        return "bash"
    return "other"


def get_tool_extension(tool: Dict[str, Any]) -> str:
    """Get file extension for tool based on language.

    Args:
        tool: The tool dictionary

    Returns:
        File extension
    """
    lang = get_tool_language(tool)
    if lang == "python":
        return ".py"
    if lang == "bash":
        return ".sh"
    return ".txt"


def build_file_structure_from_snippets(
    snippets: List[Dict[str, Any]], skill_name: str
) -> Dict[str, str]:
    """Build file structure from snippets with file: tags.

    Args:
        snippets: List of snippet dictionaries
        skill_name: The skill name

    Returns:
        Dictionary mapping file paths to content
    """
    files: Dict[str, str] = {}

    for snippet in snippets:
        file_path = extract_file_path_from_tags(snippet.get("tags"))
        if file_path:
            # Normalize the file path to remove skill name prefix
            normalized_path = normalize_file_path(file_path, skill_name)

            # Group snippets by file path
            if normalized_path in files:
                files[normalized_path] += "\n\n" + snippet["content"]
            else:
                files[normalized_path] = snippet["content"]

    return files


def build_file_structure_from_tools(
    tools: List[Dict[str, Any]],
    skill_name: str,
    tool_modules: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Build file structure from tools with file: tags.

    Args:
        tools: List of tool dictionaries
        skill_name: The skill name
        tool_modules: Dictionary mapping tool names to module content

    Returns:
        Dictionary mapping file paths to content
    """
    files: Dict[str, str] = {}

    for tool in tools:
        file_path = extract_file_path_from_tags(tool.get("tags"))
        if file_path:
            # Normalize the file path to remove skill name prefix
            normalized_path = normalize_file_path(file_path, skill_name)

            # Only write each file once (first tool wins)
            # This prevents duplicates since module_content already contains the complete file
            if normalized_path not in files:
                # Get module content
                content = ""
                if tool_modules and tool["name"] in tool_modules:
                    content = tool_modules[tool["name"]]

                files[normalized_path] = content

    return files


def export_snippets_to_skill_md(snippets: List[Dict[str, Any]]) -> str:
    """Export snippets without file: tags to SKILL.md.

    Args:
        snippets: List of snippet dictionaries

    Returns:
        Content to append to SKILL.md
    """
    content = ""

    for snippet in snippets:
        file_path = extract_file_path_from_tags(snippet.get("tags"))
        if not file_path:
            # No file path, add to SKILL.md
            content += "\n\n" + snippet["content"]

    return content


def export_tools_to_scripts(
    tools: List[Dict[str, Any]],
    skill_name: str,
    tool_modules: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Export tools without file: tags to scripts folder.

    Args:
        tools: List of tool dictionaries
        skill_name: The skill name
        tool_modules: Dictionary mapping tool names to module content

    Returns:
        Dictionary mapping file paths to content
    """
    files: Dict[str, str] = {}

    for tool in tools:
        file_path = extract_file_path_from_tags(tool.get("tags"))
        if not file_path:
            # No file path, create in scripts folder
            ext = get_tool_extension(tool)
            file_name = f"scripts/{tool['name']}{ext}"

            # Get module content
            content = ""
            if tool_modules and tool["name"] in tool_modules:
                content = tool_modules[tool["name"]]

            files[file_name] = content

    return files


def merge_file_structures(*structures: Dict[str, str]) -> Dict[str, str]:
    """Merge file structures.

    Args:
        *structures: Variable number of file structure dictionaries

    Returns:
        Merged file structure
    """
    merged: Dict[str, str] = {}

    for structure in structures:
        for path, content in structure.items():
            if path in merged:
                merged[path] += "\n\n" + content
            else:
                merged[path] = content

    return merged


def _build_file_structure(
    skill: Dict[str, Any],
    tools: List[Dict[str, Any]],
    snippets: List[Dict[str, Any]],
    tool_modules: Optional[Dict[str, str]] = None,
    name_override: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, bytes]:
    """Build the complete file structure for a skill export.

    Returns {relative_path_under_skill_name: file_content_bytes}.
    Shared by both export variants (ZIP and directory).

    ``name_override`` is forwarded to :func:`generate_skill_md` as the
    frontmatter ``name`` only — the ``<skill_name>/`` key prefix and the
    ``file:`` tag normalisation keep using the stored SBS name, so a caller
    that strips the prefix (``strip_skill_prefix``) is unaffected by it.
    """
    skill_name = skill["name"]

    snippet_files = build_file_structure_from_snippets(snippets, skill_name)
    tool_files = build_file_structure_from_tools(tools, skill_name, tool_modules)
    script_files = export_tools_to_scripts(tools, skill_name, tool_modules)

    all_files = merge_file_structures(snippet_files, tool_files, script_files)

    has_file_structure = len(all_files) > 0
    skill_md_content = generate_skill_md(
        skill,
        has_file_structure,
        snippets,
        name_override=name_override,
        metadata=metadata,
    )

    additional_snippets = export_snippets_to_skill_md(snippets)
    if additional_snippets:
        skill_md_content += additional_snippets

    has_skill_md_in_files = any(
        path.upper() == "SKILL.MD" or path.upper().endswith("/SKILL.MD")
        for path in all_files.keys()
    )

    if not has_skill_md_in_files:
        all_files["SKILL.md"] = skill_md_content
    else:
        for path in list(all_files.keys()):
            if path.upper() == "SKILL.MD" or path.upper().endswith("/SKILL.MD"):
                all_files[path] = skill_md_content + all_files[path]
                break

    result: Dict[str, bytes] = {}
    for file_path, content in all_files.items():
        normalized = content if content.endswith("\n") else content + "\n"
        result[f"{skill_name}/{file_path}"] = normalized.encode("utf-8")

    return result


# Fixed epoch for every entry. The ZIP format's own minimum (1980-01-01) rather
# than the Unix epoch, which zipfile cannot represent. Any constant works; this
# one is conventional for reproducible builds.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def build_deterministic_zip(files: Dict[str, bytes]) -> bytes:
    """Zip ``files`` so identical content always yields identical bytes.

    ``ZipFile.writestr(name, data)`` with a *string* name stamps each entry
    with ``time.localtime()``, so the same skill hashes differently on every
    call. The well-known discovery protocol publishes a sha256 of the exact
    bytes it will serve and drops any skill whose artifact does not match, so
    a wall-clock timestamp makes every skill uninstallable. Pin the mtime, the
    permission bits and the entry order instead. See docs/design/npx.md §5.1.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(files):
            info = zipfile.ZipInfo(path, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, files[path])
    return buf.getvalue()


def strip_skill_prefix(files: Dict[str, bytes], skill_name: str) -> Dict[str, bytes]:
    """Re-root ``files`` so ``SKILL.md`` sits at the archive root.

    ``_build_file_structure`` keys every path under ``<skill_name>/`` because
    both existing consumers want that container directory. The well-known
    protocol does not: it requires a root-level ``SKILL.md`` and does not strip
    a leading component. See docs/design/npx.md §5.2.
    """
    prefix = f"{skill_name}/"
    return {
        (k[len(prefix) :] if k.startswith(prefix) else k): v for k, v in files.items()
    }


def unsafe_archive_paths(files: Dict[str, bytes]) -> List[str]:
    """Return every key of ``files`` the consuming CLI would reject.

    Mirrors the CLI's ``normalizeArchivePath`` rejections (docs/design/npx.md
    §1.4): an absolute path, a ``..`` segment, a backslash, a Windows drive
    letter or a NUL byte. The CLI rejects such an entry by *throwing*, which
    aborts extraction of the whole archive — so one bad ``file:`` tag makes an
    entire skill uninstallable (§5.7). Callers exclude the skill instead.
    """
    offenders: List[str] = []
    for path in files:
        if (
            not path
            or path.startswith("/")
            or "\\" in path
            or "\x00" in path
            or ".." in path.split("/")
            or (len(path) > 1 and path[1] == ":")
        ):
            offenders.append(path)
    return sorted(offenders)


def export_skill_to_anthropic_format(
    skill: Dict[str, Any],
    tools: List[Dict[str, Any]],
    snippets: List[Dict[str, Any]],
    tool_modules: Optional[Dict[str, str]] = None,
) -> bytes:
    """Export skill to Anthropic format as a ZIP file.

    Keeps the ``<skill-name>/`` container directory — a human unzipping this
    download wants one — but builds the archive with
    :func:`build_deterministic_zip` so there is a single zip path in the tree
    and the digest tests cover both callers (docs/design/npx.md §5.13). Entries
    therefore carry a fixed 1980-01-01 mtime and sorted order.

    Args:
        skill: The skill dictionary
        tools: List of tool dictionaries
        snippets: List of snippet dictionaries
        tool_modules: Dictionary mapping tool names to module content

    Returns:
        ZIP file content as bytes
    """
    files = _build_file_structure(skill, tools, snippets, tool_modules)
    return build_deterministic_zip(files)


def export_skill_to_directory(
    skill: Dict[str, Any],
    tools: List[Dict[str, Any]],
    snippets: List[Dict[str, Any]],
    output_dir: str,
    tool_modules: Optional[Dict[str, str]] = None,
) -> None:
    """Export skill to a directory on disk.

    Writes the same file structure as export_skill_to_anthropic_format, but
    to real files instead of a ZIP archive. Used by vNFS backends.

    Args:
        skill: The skill dictionary
        tools: List of tool dictionaries
        snippets: List of snippet dictionaries
        output_dir: Destination directory path (created if absent)
        tool_modules: Dictionary mapping tool names to module content
    """
    from pathlib import Path

    files = _build_file_structure(skill, tools, snippets, tool_modules)
    for rel_path, content in files.items():
        dest = Path(output_dir) / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
