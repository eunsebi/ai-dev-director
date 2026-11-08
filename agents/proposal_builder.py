"""Builder proposes complete file contents; Director validates and applies them."""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from agents.antigravity_builder import AntigravityBuilder
from core.bootstrap import prepare_project
from core.research_runner import TriadResearchRunner, ResearchProviderCallError
from core.safety import SafetyPolicy
from core.autopilot import required_output_path as autopilot_output
from schemas.builder_result import BuilderResultSchema


class ProposalBuilder(AntigravityBuilder):
    @staticmethod
    def _repair_invalid_json_escapes(text):
        """Repair only JSON escapes that json.loads cannot legally interpret.

        AI code payloads occasionally contain a stray backslash before indentation
        (``\\n\\    code``).  Removing that one invalid marker recovers the intended
        source.  For a non-whitespace invalid escape, preserve the slash by escaping
        it.  Structure, quotes and all valid JSON escapes are left untouched.
        """
        out=[]; in_string=False; i=0; repairs=[]
        while i < len(text):
            ch=text[i]
            if ch == '"':
                # A quote is escaped when preceded by an odd slash run.
                slash_count=0; j=i-1
                while j >= 0 and text[j] == '\\': slash_count += 1; j -= 1
                if slash_count % 2 == 0: in_string = not in_string
                out.append(ch); i += 1; continue
            if in_string and ch == '\\' and i + 1 < len(text):
                nxt=text[i+1]
                if nxt not in '"\\/bfnrtu':
                    repairs.append({'offset':i, 'next':repr(nxt)})
                    if nxt.isspace():
                        i += 1; continue
                    out.append('\\\\')
                    i += 1; continue
            out.append(ch); i += 1
        return ''.join(out), repairs

    @classmethod
    def parse_proposal(cls, text):
        candidate=text; repairs=[]; original=None
        for _ in range(64):
            try:
                return json.loads(candidate), repairs
            except json.JSONDecodeError as error:
                original = original or error
                if error.msg.startswith('Extra data'):
                    # a complete JSON object followed by commentary: the object is the proposal
                    return json.JSONDecoder().raw_decode(candidate)[0], repairs + [{'offset': error.pos, 'next': 'trailing text dropped'}]
                if not error.msg.startswith('Invalid \\escape') or error.pos + 1 >= len(candidate):
                    raise original
                pos=error.pos; nxt=candidate[pos+1]
                slash_count=0; previous=pos-1
                while previous >= 0 and candidate[previous] == '\\':
                    slash_count += 1; previous -= 1
                repairs.append({'offset':pos, 'next':repr(nxt)})
                # Whitespace after a slash is always a stray formatting marker.
                # In an odd run such as JSON ``\\\\\\s`` the decoder points at
                # the final slash; dropping it preserves the already escaped slash.
                if nxt.isspace() or slash_count:
                    candidate=candidate[:pos]+candidate[pos+1:]
                else:
                    candidate=candidate[:pos]+'\\'+candidate[pos:]
        raise original or ValueError('Builder JSON repair limit exceeded')

    def __init__(self, provider='google', **kwargs):
        from core.role_settings import ALIASES, DOMAINS
        from core.env_utils import resolve_cli
        selected = ALIASES[provider.lower()]
        executable = kwargs.pop('executable', None) or resolve_cli({'openai':'codex','claude':'claude','antigravity':'agy'}[selected])
        super().__init__(executable=executable, **kwargs)
        self.provider = DOMAINS[selected].lower()
        self.agent_family = {'openai':'codex','claude':'claude','antigravity':'antigravity'}[selected]

    def get_provenance(self, exit_code=0):
        return super().get_provenance(exit_code).model_copy(update={
            'provider': self.provider, 'runtime': self.agent_family + '-cli'})

    def run(self, order, worktree_path, builder_fn=None):
        if builder_fn is not None:
            return super().run(order, worktree_path, builder_fn)
        from core import usage
        usage.set_context(role='BUILDER')
        self.last_execution_info = {'exit_code': None, 'attempts': [], 'output': ''}
        root = Path(worktree_path).resolve()
        from core.builder_context import referenced_documents
        documents = referenced_documents(order)
        sources = {}
        candidates = []
        for directory, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.')
                             and d not in {'node_modules', '__pycache__', 'data'}
                             and not (Path(directory) / d).is_symlink())
            candidates.extend(Path(directory) / name for name in sorted(names))
        editable, context = [], []
        for path in candidates:
            if not path.is_file() or path.is_symlink():
                continue
            rel = path.relative_to(root).as_posix()
            if any(p.startswith('.') or p in {'node_modules', '__pycache__', 'data'} for p in Path(rel).parts):
                continue
            if self.skip_for_prompt(rel):
                continue
            allowed, _ = SafetyPolicy.check_pre_tool_file_write(rel, str(root), order.allowed_paths, order.protected_paths)
            # Write protection must not hide baseline source from a fork/upgrade.
            readable_source = path.suffix.lower() in self.READABLE_SOURCE
            sensitive = any(word in rel.lower() for word in ('secret', 'credential', 'token', 'private_key'))
            if sensitive or path.stat().st_size >= 100_000:
                continue
            if allowed:
                editable.append((rel, path))
            elif readable_source:
                context.append((rel, path))
        # The files the ORDER lets the Builder change come first (a budget spent on unrelated files once hid the only
        # file a milestone had to edit), ranked: required outputs, then files the ORDER/repair notes name, then the
        # rest smallest first. An oversized prompt is its own failure (empty responses, 600 s timeouts), so what does
        # not fit is listed by path only and the Builder is told not to rewrite it.
        order_text = '\n'.join([order.goal, order.context or '', *order.acceptance_criteria])
        wanted = {p for p in (autopilot_output(r) for r in order.required_outputs) if p}
        notes = self.repair_notes(order.context)
        if notes:
            focused = self._repair_run(order, root, notes, editable, context, candidates, documents)
            if focused is not None:
                return focused

        # A narrow allowed_paths entry (e.g. "ea/**" with one file) names its files as well as the goal text does.
        narrow = set()
        for pattern in order.allowed_paths or []:
            hits = [rel for rel, _ in editable if SafetyPolicy.is_path_allowed(rel, [pattern])]
            if 0 < len(hits) <= self.NARROW_SCOPE_FILES:
                narrow.update(hits)

        def rank(item):
            rel, path = item
            named = rel in order_text or Path(rel).name in order_text or rel in narrow
            return (0 if rel in wanted else 1 if named else 2, path.stat().st_size, rel)
        editable.sort(key=rank)
        context.sort(key=lambda item: (not (item[0] in order_text or Path(item[0]).name in order_text),
                                       'test' in Path(item[0]).name.lower() or '/tests/' in '/' + item[0], item[0]))
        omitted, not_shown = [], []
        named = [(rel, path) for rel, path in editable if rank((rel, path))[0] < 2]
        if named:
            # [fork 2026-11-08] First build, focused: full text for the files the ORDER names, then the files those
            # (and the ORDER) use by type name, within a smaller budget; every other file by path only. Sending
            # all writable files (old tests, asset JSON, unrelated screens: ~190 KB) made a slow, 600 s-timeout
            # prone call and invited rewrites of files the ORDER never asked for.
            used = 0
            for rel, path in named:
                text = path.read_text(encoding='utf-8', errors='replace')
                if used + len(text) <= self.EDITABLE_BUDGET:
                    sources[rel] = text
                    used += len(text)
                else:
                    omitted.append(rel)
            refs_text = order_text + '\n' + '\n'.join(sources.values())
            used = 0
            for rel, path in [i for i in editable if i[0] not in sources and i[0] not in omitted] + context:
                if rel in order_text or Path(rel).name in order_text or self.referenced(rel, refs_text):
                    text = path.read_text(encoding='utf-8', errors='replace')
                    if used + len(text) <= self.RELATED_BUDGET:
                        sources[rel] = text
                        used += len(text)
                        continue
                if (rel, path) in editable:
                    not_shown.append(rel)
        else:
            for budget, group in ((self.EDITABLE_BUDGET, editable), (self.CONTEXT_BUDGET, context)):
                used = 0
                for rel, path in group:
                    text = path.read_text(encoding='utf-8', errors='replace')
                    if used + len(text) <= budget:
                        sources[rel] = text
                        used += len(text)
                    elif group is editable:
                        omitted.append(rel)
        self.last_context_files = list(sources)          # in priority order: editable first
        self.last_omitted_files = omitted
        prompt = ('You are the implementation Builder. Do not use tools. All source context is supplied below. '
                  'Return ONLY JSON: {"files":[{"path":"relative/file.py","content":"complete file contents"}],'
                  '"edits":[{"path":"existing/file.py","old":"exact text copied from that file","new":"replacement text"}],'
                  '"summary":"..."}. Prefer "edits" for changes to existing files - each "old" must appear EXACTLY ONCE in '
                  'the file as shown below (copy it verbatim with indentation, long enough to be unique); use "files" for '
                  'new files or complete rewrites. Keep the reply compact. '
                  'Implement the ORDER and real behavior tests. Preserve existing behavior. No placeholder implementations. '
                  'Return only changed files. No deletion operations. Stay within file and diff budgets. '
                  'HEAD feedback and outstanding reviewer findings in context must be resolved. '
                  'Director host Python is ' + sys.version.split()[0] + '; platform is ' + sys.platform +
                  '. Honor any project-specific runtime requirements in the ORDER.\nORDER:\n' + order.model_dump_json() +
                  '\nREFERENCE DOCUMENTS:\n' + json.dumps(documents, ensure_ascii=False) +
                  '\nREFERENCE SOURCE FILES (read-only unless ORDER allows writes; omitted files are not necessarily absent):\n' + json.dumps(sources, ensure_ascii=False)
                  + ('\nWRITABLE FILES NOT SHOWN (too large for this prompt; they exist - do NOT rewrite or edit them): '
                     + json.dumps(omitted) if omitted else '')
                  + ('\nOTHER FILES IN SCOPE (exist, not shown, not needed for this ORDER: leave them unchanged): '
                     + json.dumps(not_shown[:300]) if not_shown else ''))
        return self._invoke_and_apply(prompt, order, root, repair=False)

    def _invoke_and_apply(self, prompt, order, root, repair):
        domain = self.provider.upper()
        runner = TriadResearchRunner(executables={domain: self.executable}, timeout=self.timeout,
                                     no_output_timeout=self.no_progress_timeout)
        with tempfile.TemporaryDirectory(prefix='director-builder-') as tmp:
            prepare_project(tmp)
            try:
                result = runner.invoke(domain, prompt, tmp, 'BUILDING')
            except ResearchProviderCallError as exc:
                self.last_execution_info = dict(exc.meta)
                self.last_execution_info.update(error=str(exc), output='', context_files=self.last_context_files,
                    attempts=getattr(exc, 'attempts', []), exit_code=exc.meta.get('exit_code'),
                    execution_duration_sec=sum(a.get('duration_sec', 0) for a in getattr(exc, 'attempts', [])))
                raise
        self.last_execution_info = {'invocation_command': result['invocation'], 'exit_code': result['exit_code'],
                                    'execution_duration_sec': result['duration_sec'], 'output': result['output'],
                                    'attempts': result.get('attempts', []), 'attempt_number': result.get('attempt_number', 1),
                                    'context_files': self.last_context_files, 'prompt_chars': len(prompt),
                                    'omitted_files': self.last_omitted_files}
        text = result['output'].strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        proposal, repairs = self.parse_proposal(text)
        if repairs:
            self.last_execution_info['json_repairs'] = repairs
        if self.provider == 'anthropic' and isinstance(proposal, dict) and proposal.get('type') == 'result':
            if proposal.get('is_error'):
                raise ValueError('Claude Builder returned an error result')
            text = proposal.get('result', '').strip()
            if text.startswith('```'):
                text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
            proposal, envelope_repairs = self.parse_proposal(text)
            if envelope_repairs:
                self.last_execution_info.setdefault('json_repairs', []).extend(envelope_repairs)
        if not isinstance(proposal, dict):
            raise ValueError('Builder must return a JSON object')
        files = proposal.get('files') or []
        edits = proposal.get('edits') or []
        if not isinstance(files, list) or not isinstance(edits, list):
            raise ValueError('Builder must return a bounded nonempty files list')
        if not files and not edits and any(m in (order.context or '') for m in ('HEAD REPAIR ORDER', 'COMPILE FIX ORDER')):
            # In a correction round "nothing needs to change" is a legitimate answer: the Director re-verifies and the
            # Reviewer/HEAD judge the existing changes again (the repair budget bounds this).
            return BuilderResultSchema(status='COMPLETED', files_modified=[], commands_run=[result['invocation']],
                                       summary='No further changes proposed: ' + str(proposal.get('summary', ''))[:500])
        new_contents = self.resolve_changes(files, edits, root, order, repair=repair)
        if not new_contents or len(new_contents) > order.max_files_changed:
            raise ValueError('Builder must return a bounded nonempty files list')
        # The complete batch was validated (paths, exact edit anchors) before the first file is written.
        for rel, content in new_contents.items():
            self.pre_tool_write_file(rel, content, str(root), order)
        return BuilderResultSchema(status='COMPLETED', files_modified=list(new_contents),
                                   commands_run=[result['invocation']], summary=proposal.get('summary', 'Files implemented'))

    EDITABLE_BUDGET = 140_000
    CONTEXT_BUDGET = 60_000

    READABLE_SOURCE = {'.py', '.js', '.ts', '.tsx', '.jsx', '.md', '.toml', '.swift', '.m', '.mm', '.h', '.c', '.cc',
                       '.cpp', '.kt', '.kts', '.java', '.go', '.rs', '.cs', '.rb', '.php', '.dart', '.vue', '.svelte'}
    RELATED_BUDGET = 40_000
    NARROW_SCOPE_FILES = 4
    SKIP_NAMES = {'claude.md', 'agents.md', 'gemini.md', 'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
                  'package.resolved', 'podfile.lock', 'cargo.lock', 'poetry.lock'}
    SKIP_DIR_SUFFIXES = ('.xcassets', '.xcodeproj', '.xcworkspace', '.framework', '.bundle', '.lproj')
    BINARY_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.heic', '.ico', '.icns', '.pdf', '.zip', '.gz',
                       '.mp3', '.mp4', '.mov', '.wav', '.ttf', '.otf', '.woff', '.woff2', '.a', '.dylib', '.so',
                       '.o', '.car', '.mlmodel', '.sqlite', '.db', '.p12', '.mobileprovision', '.keystore'}

    @classmethod
    def skip_for_prompt(cls, rel):
        """Never worth prompt space: binaries, asset catalogs / project bundles, lock files, agent instruction files
        (CLAUDE.md can hold server addresses and must not go to another provider)."""
        p = Path(rel)
        if p.name.lower() in cls.SKIP_NAMES or p.suffix.lower() in cls.BINARY_SUFFIXES:
            return True
        return any(part.lower().endswith(cls.SKIP_DIR_SUFFIXES) for part in p.parts[:-1])

    @staticmethod
    def referenced(rel, text):
        """A source file whose stem is used as a type/module name in `text` (CamelCase stems as whole words)."""
        stem = Path(rel).stem
        if len(stem) < 4 or not re.search(r'[A-Z]', stem):
            return False
        return re.search(r'(?<![A-Za-z0-9_])' + re.escape(stem) + r'(?![A-Za-z0-9_])', text) is not None

    REPAIR_MARKERS = ('\nHEAD REPAIR ORDER: ', '\nCOMPILE FIX ORDER: ')
    REPAIR_EDITABLE_BUDGET = 90_000
    REPAIR_CONTEXT_BUDGET = 40_000
    REPAIR_MAX_FOCUS = 12
    REWRITE_LIMIT = 3_000          # in a repair round an existing file larger than this is changed by edits only

    @classmethod
    def repair_notes(cls, context):
        """The Director's repair instructions in ORDER.context: {'head_repair': {...}, 'compile_fix': {...}} or {}."""
        text = context or ''
        out = {}
        for key, marker in zip(('head_repair', 'compile_fix'), cls.REPAIR_MARKERS):
            i = text.rfind(marker)
            if i < 0:
                continue
            try:
                out[key] = json.JSONDecoder().raw_decode(text[i + len(marker):].lstrip())[0]
            except ValueError:
                out[key] = {'raw': text[i + len(marker):][:4000]}
        return out

    @staticmethod
    def defects(notes):
        """The concrete things to fix, in order: compiler errors, HEAD's steps, reviewer P0/P1."""
        items = []
        for n in (notes.get('compile_fix') or {}, notes.get('head_repair') or {}):
            items += [str(e) for e in n.get('build_errors') or []]
        head = (notes.get('head_repair') or {}).get('head') or {}
        items += [str(x) for x in head.get('next_steps') or []]
        review = (notes.get('head_repair') or {}).get('review') or {}
        items += [str(x) for x in (review.get('p0') or []) + (review.get('p1') or [])]
        if not items and (notes.get('head_repair') or {}).get('raw'):
            items.append(notes['head_repair']['raw'])
        seen = []
        for it in items:
            if it not in seen:
                seen.append(it)
        return seen

    @staticmethod
    def _changed_files(root):
        try:
            out = subprocess.run(['git', '-c', 'core.quotepath=false', 'status', '--porcelain', '-uall'], cwd=root,
                                 capture_output=True, text=True, timeout=30).stdout
        except Exception:
            return []
        files = []
        for line in out.splitlines():
            if len(line) > 3:
                rel = line[3:].strip().strip('"')
                files.append(rel.split(' -> ', 1)[-1].replace('\\', '/'))
        return files

    @classmethod
    def focus_files(cls, defects, candidates_rel, changed):
        """Files the defects name (path, file name, or a CamelCase type name that is a file stem), then the files
        this run already changed."""
        text = '\n'.join(defects)
        named = []
        for rel in candidates_rel:
            name = Path(rel).name
            stem = Path(rel).stem
            hit = rel in text or name in text
            if not hit and len(stem) >= 6 and re.search(r'[A-Z]', stem[1:]) and re.search(r'\b' + re.escape(stem) + r'\b', text):
                hit = True
            if hit:
                named.append(rel)
        # most specific first: files with an error line, then the others named, then this run's changes
        errs = [rel for rel in named if re.search(re.escape(rel) + r':\d+', text)]
        ordered = errs + [r for r in named if r not in errs] + [r for r in changed if r in candidates_rel]
        out = []
        for r in ordered:
            if r not in out:
                out.append(r)
        return out[:cls.REPAIR_MAX_FOCUS]

    @staticmethod
    def error_snippets(defects, root, radius=10, limit=12):
        """Numbered source lines around each `path:line:` compiler error, so the fix can be an exact edit."""
        out, seen = [], set()
        for d in defects:
            m = re.match(r'^([^:\s][^:]*):(\d+)(?::\d+)?: (?:fatal )?error: ', d)
            if not m or (m.group(1), m.group(2)) in seen:
                continue
            seen.add((m.group(1), m.group(2)))
            path = Path(root) / m.group(1)
            try:
                lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
            except OSError:
                continue
            n = int(m.group(2))
            lo, hi = max(1, n - radius), min(len(lines), n + radius)
            body = '\n'.join(f'{i:5d}{">" if i == n else " "} {lines[i - 1]}' for i in range(lo, hi + 1))
            out.append(f'--- {m.group(1)} (error at line {n})\n{body}')
            if len(out) >= limit:
                break
        return out

    def _repair_run(self, order, root, notes, editable, context, candidates, documents):
        """Repair round: send only the defects and the files they concern, and ask for minimal edits. The earlier
        rounds' work is already in the worktree; re-sending the whole project (~190 KB) with "implement the ORDER"
        made the Builder re-do the work, slowly (600 s timeouts) and with new mistakes. Returns None to fall back to
        the full prompt when no file can be tied to the defects."""
        defects = self.defects(notes)
        if not defects:
            return None
        all_rel = [p.relative_to(root).as_posix() for p in candidates if p.is_file() and not p.is_symlink()]
        changed = self._changed_files(str(root))
        focus = self.focus_files(defects, all_rel, changed)
        editable_rel = {rel for rel, _ in editable}
        readable_rel = {rel for rel, _ in context}
        if not any(f in editable_rel for f in focus):
            return None
        sources, readonly, omitted_focus = {}, {}, []
        used_e = used_c = 0
        for rel in focus:
            path = root / rel
            if rel in editable_rel:
                text = path.read_text(encoding='utf-8', errors='replace')
                if used_e + len(text) <= self.REPAIR_EDITABLE_BUDGET:
                    sources[rel] = text
                    used_e += len(text)
                else:
                    omitted_focus.append(rel)
            elif rel in readable_rel:
                text = path.read_text(encoding='utf-8', errors='replace')
                if used_c + len(text) <= self.REPAIR_CONTEXT_BUDGET:
                    readonly[rel] = text
                    used_c += len(text)
        not_shown = [rel for rel in sorted(editable_rel) if rel not in sources]
        self.last_context_files = list(sources) + list(readonly)
        self.last_omitted_files = omitted_focus
        snippets = self.error_snippets(defects, root)
        order_brief = {'goal': order.goal, 'acceptance_criteria': order.acceptance_criteria,
                       'allowed_paths': order.allowed_paths, 'protected_paths': order.protected_paths,
                       'max_files_changed': order.max_files_changed}
        prompt = (
            'You are the implementation Builder in a REPAIR round. Do not use tools. '
            'The ORDER was already implemented in earlier rounds: that work is in the files below. '
            'Do NOT re-implement, restructure or rewrite anything. Fix ONLY the DEFECTS listed, with the smallest '
            'possible change. Return ONLY JSON: {"edits":[{"path":"existing/file","old":"exact text copied from that '
            'file","new":"replacement text"}],"files":[{"path":"new/file","content":"..."}],"summary":"..."}. '
            'Use "edits" for every existing file (each "old" must appear EXACTLY ONCE, copied verbatim with indentation; '
            'line numbers in ERROR CONTEXT are NOT part of the file). Use "files" only for a genuinely new file. '
            'Keep every acceptance criterion and existing behavior. If a defect can only be fixed in a file you may not '
            'write, change nothing for it and name that file in summary. '
            'Director host Python is ' + sys.version.split()[0] + '; platform is ' + sys.platform + '.'
            + '\nDEFECTS TO FIX (in priority order):\n' + json.dumps(defects, ensure_ascii=False, indent=1)
            + ('\nERROR CONTEXT (numbered lines, > marks the error line):\n' + '\n'.join(snippets) if snippets else '')
            + '\nORDER (for reference only - already implemented):\n' + json.dumps(order_brief, ensure_ascii=False)
            + ('\nREFERENCE DOCUMENTS:\n' + json.dumps(documents, ensure_ascii=False) if documents else '')
            + '\nFILES YOU MAY EDIT (current content):\n' + json.dumps(sources, ensure_ascii=False)
            + ('\nREAD-ONLY FILES NAMED BY THE DEFECTS (outside allowed_paths; do not edit):\n'
               + json.dumps(readonly, ensure_ascii=False) if readonly else '')
            + ('\nOTHER WRITABLE FILES (exist, not shown: leave them unchanged): ' + json.dumps(not_shown[:200])
               if not_shown else ''))
        result = self._invoke_and_apply(prompt, order, root, repair=True)
        self.last_execution_info['repair_focus'] = {'defects': len(defects), 'focus': focus, 'prompt_chars': len(prompt)}
        return result

    @staticmethod
    def resolve_changes(files, edits, root, order, repair=False):
        """Full-file proposals and exact search/replace edits -> {path: new content}. Nothing is written here; any
        denied path, duplicate, or edit anchor that is missing or ambiguous rejects the whole batch."""
        root = Path(root)
        new_contents, seen = {}, set()

        def allowed(rel):
            ok, reason = SafetyPolicy.check_pre_tool_file_write(rel, str(root), order.allowed_paths, order.protected_paths)
            if not ok:
                raise ValueError('Proposed path denied: ' + reason)
        for item in files:
            rel, content = item.get('path'), item.get('content')
            if not isinstance(rel, str) or not isinstance(content, str) or len(content) > 300_000 or rel.casefold() in seen:
                raise ValueError('Invalid/duplicate proposed file')
            seen.add(rel.casefold())
            allowed(rel)
            existing = root / rel
            if repair and existing.is_file() and existing.stat().st_size > ProposalBuilder.REWRITE_LIMIT:
                raise ValueError(f'REPAIR_REWRITE: {rel} exists; in a repair round change existing files with exact '
                                 f'"edits" only, never a complete rewrite')
            new_contents[rel] = content
        for edit in edits:
            rel, old, new = edit.get('path'), edit.get('old'), edit.get('new')
            if not isinstance(rel, str) or not isinstance(old, str) or not old or not isinstance(new, str):
                raise ValueError('Invalid edit: path, non-empty old and new text are required')
            allowed(rel)
            if rel not in new_contents:
                target = root / rel
                if not target.is_file():
                    raise ValueError(f'Edit target does not exist: {rel}')
                new_contents[rel] = target.read_text(encoding='utf-8', errors='replace')
            count = new_contents[rel].count(old)
            if count != 1:
                raise ValueError(f'Edit for {rel} must match exactly once (found {count}); copy "old" verbatim and make it unique')
            new_contents[rel] = new_contents[rel].replace(old, new, 1)
        return new_contents
