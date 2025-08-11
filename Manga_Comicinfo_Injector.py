import os
import re
import json
import io
import shutil
import tempfile
import zipfile
import subprocess
from pathlib import Path
import requests
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import customtkinter
from customtkinter import CTkImage
from PIL import Image
import xml.etree.ElementTree as ET
from datetime import datetime

# Config
ANILIST_ENDPOINT = "https://graphql.anilist.co"
CACHE_FILE = "manga_cache.json"
CONVERSION_LOG = "converted_cbr.log"
COMICINFO_NAME = "ComicInfo.xml"
COVER_NAME = "cover.jpg"
FILE_EXTS = [".cbz", ".cbr"]

ANILIST_QUERY = """
query ($search: String, $page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    media(search: $search, type: MANGA) {
      id
      title { romaji english native }
      startDate { year }
      description(asHtml: false)
      genres
      coverImage { large extraLarge }
      staff {
        edges {
          role
          node {
            name { full }
            primaryOccupations
          }
        }
      }
    }
  }
}
"""
_POSITIVE_OCC_KEYWORDS = [
    "story", "writer", "author", "manga", "art", "illustrator",
    "illustration", "original creator", "creator", "character design"
]
_NEGATIVE_OCC_KEYWORDS = [
    "translate", "translator", "translation", "editor",
    "clean", "redraw", "redrawer", "letterer", "proof"
]

def _edge_is_creator(edge):
    """
    Return True if the staff edge probably refers to a story/author/artist-type role.
    Return False if it's clearly a translator/editor/etc.
    """
    node = edge.get("node", {}) or {}
    name = node.get("name", {}).get("full")
    if not name:
        return False

    occupations = node.get("primaryOccupations") or []
    role = edge.get("role") or ""
    combined = " ".join(occupations + [role]).lower()

    # If any negative token present, reject immediately
    if any(neg in combined for neg in _NEGATIVE_OCC_KEYWORDS):
        return False

    # If any positive token present, accept
    if any(pos in combined for pos in _POSITIVE_OCC_KEYWORDS):
        return True

    # Otherwise unknown (return False here; caller may fall back to a looser rule)
    return False

# Utility functions
def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_cache(cache):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def append_conversion_log(orig, new):
    try:
        now = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
        line = f"{now} Converted: {orig} -> {new}\n"
        with open(CONVERSION_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

def strip_html(text):
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text)

def anilist_search(term, per_page=6):
    variables = {"search": term, "page": 1, "perPage": per_page}
    try:
        r = requests.post(ANILIST_ENDPOINT, json={"query": ANILIST_QUERY, "variables": variables}, timeout=20)
        r.raise_for_status()
        data = r.json()
        media = data.get("data", {}).get("Page", {}).get("media", [])
        results = []
        for m in media:
            staff = []
            for e in m.get("staff", {}).get("edges", []):
                name = e.get("node", {}).get("name", {}).get("full")
                if name and _edge_is_creator(e):
                    staff.append(name)

            # If strict matching returned nothing, fallback to "everyone except excluded roles"
            if not staff:
                for e in m.get("staff", {}).get("edges", []):
                    node = e.get("node", {}) or {}
                    name = node.get("name", {}).get("full")
                    occupations = node.get("primaryOccupations") or []
                    role = e.get("role") or ""
                    combined = " ".join(occupations + [role]).lower()
                    if name and not any(neg in combined for neg in _NEGATIVE_OCC_KEYWORDS):
                        staff.append(name)

            results.append({
                "id": m.get("id"),
                "title_romaji": m.get("title", {}).get("romaji"),
                "title_english": m.get("title", {}).get("english"),
                "title_native": m.get("title", {}).get("native"),
                "year": m.get("startDate", {}).get("year"),
                "description": strip_html(m.get("description")),
                "genres": m.get("genres", []),
                "cover_large": m.get("coverImage", {}).get("large"),
                "cover_xl": m.get("coverImage", {}).get("extraLarge"),
                "staff": staff
            })
        return results
    except Exception:
        return []

def normalize_name(name):
    s = name.lower()
    s = re.sub(r"\b(19|20)\d{2}\b", "", s)
    s = re.sub(r"[\[\]\(\)\{\}_]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def guess_folder_title(folder_path):
    folder_name = Path(folder_path).name
    title = re.sub(r"\b(19|20)\d{2}\b$", "", folder_name).strip()
    return title

def download_image(url):
    if not url:
        return None
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.content
    except Exception:
        return None

def build_comicinfo(metadata, prefer="romaji"):
    root = ET.Element("ComicInfo")
    if prefer == "english":
        chosen = metadata.get("title_english") or metadata.get("title_romaji") or metadata.get("title_native") or ""
    else:
        chosen = metadata.get("title_romaji") or metadata.get("title_english") or metadata.get("title_native") or ""
    ET.SubElement(root, "Title").text = chosen
    ET.SubElement(root, "Series").text = metadata.get("title_romaji") or metadata.get("title_english") or ""
    if metadata.get("year"):
        ET.SubElement(root, "Year").text = str(metadata.get("year"))
    writers = metadata.get("staff", []) or []
    # Always create Writer tag, even if empty
    ET.SubElement(root, "Writer").text = ", ".join(writers) if writers else ""
    genres = metadata.get("genres", []) or []
    if genres:
        ET.SubElement(root, "Genre").text = ", ".join(genres)
    summary = metadata.get("description", "")
    if summary:
        ET.SubElement(root, "Summary").text = summary
    ET.SubElement(root, "CoverImage").text = COVER_NAME
    tree = ET.ElementTree(root)
    bio = io.BytesIO()
    tree.write(bio, encoding="utf-8", xml_declaration=True)
    return bio.getvalue()

def cbz_has_entry(path, name):
    try:
        with zipfile.ZipFile(path, "r") as z:
            return name in z.namelist()
    except Exception:
        return False

def inject_into_cbz(path, entries):
    try:
        existing = set()
        with zipfile.ZipFile(path, "r") as zin:
            existing = set(zin.namelist())
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        with zipfile.ZipFile(path, "r") as zin, zipfile.ZipFile(tmp.name, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                zout.writestr(item, data)
            for name, data in entries.items():
                if name not in existing:
                    zout.writestr(name, data)
        shutil.move(tmp.name, path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp.name):
                os.remove(tmp.name)
        except Exception:
            pass
        return False

def extract_cbr_to_temp(path, tmpdir):
    commands = [
        ["7z", "x", path, f"-o{tmpdir}", "-y"],
        ["unrar", "x", path, tmpdir],
        ["rar", "x", path, tmpdir]
    ]
    for cmd in commands:
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)
            return True
        except Exception:
            continue
    return False

def repackage_dir_to_cbz(src_dir, dest_cbz_path):
    tmpfile = tempfile.NamedTemporaryFile(delete=False)
    tmpfile.close()
    try:
        with zipfile.ZipFile(tmpfile.name, "w", compression=zipfile.ZIP_STORED) as z:
            for root, dirs, files in os.walk(src_dir):
                files.sort()
                rel_root = os.path.relpath(root, src_dir)
                for f in files:
                    full = os.path.join(root, f)
                    arcname = f if rel_root == "." else os.path.join(rel_root, f)
                    arcname = arcname.replace(os.path.sep, "/")
                    with open(full, "rb") as fh:
                        z.writestr(arcname, fh.read())
        shutil.move(tmpfile.name, dest_cbz_path)
        return True
    except Exception:
        try:
            if os.path.exists(tmpfile.name):
                os.remove(tmpfile.name)
        except Exception:
            pass
        return False

def convert_cbr_to_cbz(path, entries_to_add):
    tmpdir = tempfile.mkdtemp()
    try:
        if not extract_cbr_to_temp(path, tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)
            return None
        for name, data in entries_to_add.items():
            target = os.path.join(tmpdir, name)
            if not os.path.exists(target):
                with open(target, "wb") as f:
                    f.write(data)
        base = os.path.splitext(path)[0]
        dest_cbz = base + ".cbz"
        if not repackage_dir_to_cbz(tmpdir, dest_cbz):
            shutil.rmtree(tmpdir, ignore_errors=True)
            return None
        try:
            os.remove(path)
        except Exception:
            pass
        append_conversion_log(os.path.basename(path), os.path.basename(dest_cbz))
        shutil.rmtree(tmpdir, ignore_errors=True)
        return dest_cbz
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return None

class AniApp:
    def __init__(self):
        customtkinter.set_appearance_mode("Dark")
        customtkinter.set_default_color_theme("blue")
        self.root = customtkinter.CTk()
        self.root.title("Manga ComicInfo Injector")
        self.root.geometry("1100x700")
        self.cache = load_cache()
        self.folders = []

        self.create_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def create_ui(self):
        self.sidebar = customtkinter.CTkFrame(self.root, width=320)
        self.sidebar.pack(side="left", fill="y", padx=12, pady=12)
        self.main = customtkinter.CTkFrame(self.root)
        self.main.pack(side="right", fill="both", expand=True, padx=12, pady=12)

        # Header label
        lbl = customtkinter.CTkLabel(self.sidebar, text="Manga folders", font=customtkinter.CTkFont(size=16, weight="bold"))
        lbl.pack(pady=(6, 6), anchor="w", padx=6)

        # Listbox replacement: use CTkTextbox with readonly and custom selection logic or use a CTkScrollableFrame with labels
        # Here, let's use CTkTextbox in readonly mode to list folders
        self.folder_list = customtkinter.CTkTextbox(self.sidebar, height=250, width=280)
        self.folder_list.configure(state="disabled")
        self.folder_list.pack(fill="x", padx=6, pady=6)

        # Buttons frame
        btn_frame = customtkinter.CTkFrame(self.sidebar)
        btn_frame.pack(pady=6, fill="x")
        add_btn = customtkinter.CTkButton(btn_frame, text="Add Folder", command=self.add_folder)
        add_btn.pack(side="left", padx=6)
        add_parent_btn = customtkinter.CTkButton(btn_frame, text="Add Parent Folder", command=self.add_parent_folder)
        add_parent_btn.pack(side="left", padx=6)

        remove_btn = customtkinter.CTkButton(self.sidebar, text="Remove All", command=self.remove_all)
        remove_btn.pack(padx=6, pady=6)
        start_btn = customtkinter.CTkButton(self.sidebar, text="Start", command=self.start)
        start_btn.pack(padx=6, pady=6)

        # Title preference label
        pref_label = customtkinter.CTkLabel(self.sidebar, text="Title prefer", font=customtkinter.CTkFont(size=14, weight="bold"))
        pref_label.pack(anchor="w", padx=6, pady=(12, 6))

        # Default to English now
        self.title_pref = customtkinter.StringVar(value="english")
        r1 = customtkinter.CTkRadioButton(self.sidebar, text="romaji", variable=self.title_pref, value="romaji")
        r1.pack(anchor="w", padx=6)
        r2 = customtkinter.CTkRadioButton(self.sidebar, text="english", variable=self.title_pref, value="english")
        r2.pack(anchor="w", padx=6)

        # New toggle: add covers or not
        self.add_covers_var = customtkinter.BooleanVar(value=True)
        cover_chk = customtkinter.CTkCheckBox(self.sidebar, text="Add covers", variable=self.add_covers_var)
        cover_chk.pack(anchor="w", padx=6, pady=(8, 6))

        # Progress bar
        self.progress = customtkinter.CTkProgressBar(self.main)
        self.progress.pack(fill="x", padx=6, pady=6)

        # Log text area (use CTkTextbox for consistent theme)
        self.log = customtkinter.CTkTextbox(self.main, height=400)
        self.log.configure(state="disabled")
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

    def log_msg(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self.root.update()

    def update_folder_list(self):
        self.folder_list.configure(state="normal")
        self.folder_list.delete("0.0", "end")
        for folder in self.folders:
            self.folder_list.insert("end", folder + "\n")
        self.folder_list.configure(state="disabled")

    def add_folder(self):
        d = filedialog.askdirectory(mustexist=True)
        if d and d not in self.folders:
            self.folders.append(d)
            self.update_folder_list()

    def add_parent_folder(self):
        parent = filedialog.askdirectory(mustexist=True)
        if not parent:
            return
        added = False
        for entry in sorted(os.listdir(parent)):
            full = os.path.join(parent, entry)
            if os.path.isdir(full) and full not in self.folders:
                self.folders.append(full)
                added = True
        if added:
            self.update_folder_list()

    def remove_all(self):
        if messagebox.askyesno("Confirm", "Remove all folders from the list?"):
            self.folders.clear()
            self.update_folder_list()

    # prompt_picker is large and continues in Part 2
    def prompt_picker(self, candidates, normalized):
        chosen = {"value": None}

        exact_matches = [c for c in candidates if normalize_name(c.get("title_romaji") or c.get("title_english") or "") == normalized]
        if len(exact_matches) == 1:
            return exact_matches[0]

        win = customtkinter.CTkToplevel(self.root)
        win.title("Choose AniList match")
        win.geometry("1400x650")

        # URL input section at top
        url_frame = customtkinter.CTkFrame(win)
        url_frame.pack(fill="x", padx=6, pady=6)
        url_label = customtkinter.CTkLabel(url_frame, text="Paste AniList URL:", width=120)
        url_label.pack(side="left", padx=(0, 6))
        url_entry = customtkinter.CTkEntry(url_frame)
        url_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        fetch_btn = customtkinter.CTkButton(url_frame, text="Fetch", width=80)
        fetch_btn.pack(side="left")

        # Scrollable frame setup
        container = customtkinter.CTkFrame(win)
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(container, bg="#2b2b2b", highlightthickness=0)
        scrollbar = customtkinter.CTkScrollbar(container, orientation="vertical", command=canvas.yview)
        frame = customtkinter.CTkFrame(canvas)

        frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Mousewheel scrolling cross-platform
        def _on_mousewheel(event):
            if not canvas.winfo_exists():
                return  # Canvas destroyed, ignore scroll

            try:
                if event.num == 4:  # Linux scroll up
                    canvas.yview_scroll(-1, "units")
                elif event.num == 5:  # Linux scroll down
                    canvas.yview_scroll(1, "units")
                else:  # Windows / macOS
                    canvas.yview_scroll(int(-1*(event.delta/120)), "units")
            except tk.TclError:
                pass  # Canvas was destroyed mid-scroll

        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        canvas.bind_all("<Button-4>", _on_mousewheel)
        canvas.bind_all("<Button-5>", _on_mousewheel)

        # Function to fetch manga by AniList ID from URL
        def fetch_from_url():
            url = url_entry.get().strip()
            if not url:
                return
            m = re.search(r'anilist\.co\/manga\/(\d+)', url)
            if not m:
                messagebox.showerror("Error", "Invalid AniList manga URL")
                return
            manga_id = m.group(1)

            query_by_id = """
            query ($id: Int) {
              Media(id: $id, type: MANGA) {
                id
                title { romaji english native }
                startDate { year }
                description(asHtml: false)
                genres
                coverImage { large extraLarge }
                staff {
                  edges {
                    node {
                      name { full }
                      primaryOccupations
                    }
                  }
                }
              }
            }
            """
            try:
                r = requests.post(ANILIST_ENDPOINT, json={"query": query_by_id, "variables": {"id": int(manga_id)}}, timeout=20)
                r.raise_for_status()
                data = r.json()
                media = data.get("data", {}).get("Media")
                if media:
                    candidates.clear()

                    edges = media.get("staff", {}).get("edges", [])
                    staff = []
                    for e in edges:
                        name = e.get("node", {}).get("name", {}).get("full")
                        if name and _edge_is_creator(e):
                            staff.append(name)

                    # Fallback: if no staff matched, include all except translators/editors/etc.
                    if not staff:
                        for e in edges:
                            node = e.get("node", {}) or {}
                            name = node.get("name", {}).get("full")
                            occupations = node.get("primaryOccupations") or []
                            role = e.get("role") or ""
                            combined = " ".join(occupations + [role]).lower()
                            if name and not any(neg in combined for neg in _NEGATIVE_OCC_KEYWORDS):
                                staff.append(name)

                    candidates.append({
                        "id": media.get("id"),
                        "title_romaji": media.get("title", {}).get("romaji"),
                        "title_english": media.get("title", {}).get("english"),
                        "title_native": media.get("title", {}).get("native"),
                        "year": media.get("startDate", {}).get("year"),
                        "description": strip_html(media.get("description")),
                        "genres": media.get("genres", []),
                        "cover_large": media.get("coverImage", {}).get("large"),
                        "cover_xl": media.get("coverImage", {}).get("extraLarge"),
                        "staff": staff
                    })
                    for widget in frame.winfo_children():
                        widget.destroy()
                    build_candidate_cards()
                else:
                    messagebox.showinfo("Not found", "No manga found for given AniList ID")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to fetch data: {e}")

        fetch_btn.configure(command=fetch_from_url)

        def choose(item):
            chosen["value"] = item
            win.destroy()

        def build_candidate_cards():
            for c in candidates:
                card = customtkinter.CTkFrame(frame, corner_radius=8, fg_color="#222222", border_width=1, border_color="#444444")
                card.pack(fill="x", pady=6, padx=6)

                left = customtkinter.CTkFrame(card, width=120, height=180, fg_color="#222222")
                left.pack(side="left", padx=6, pady=6)
                img_data = None
                url = c.get("cover_large") or c.get("cover_xl")
                if url:
                    img_data = download_image(url)
                if img_data:
                    try:
                        im = Image.open(io.BytesIO(img_data))
                        im.thumbnail((120, 180))
                        photo = CTkImage(light_image=im, dark_image=im, size=(120, 180))
                        lblimg = customtkinter.CTkLabel(left, image=photo, text="")
                        lblimg.image = photo
                        lblimg.pack()
                    except Exception:
                        customtkinter.CTkLabel(left, text="No cover", fg_color="#222222", text_color="#ffffff").pack()
                else:
                    customtkinter.CTkLabel(left, text="No cover", fg_color="#222222", text_color="#ffffff").pack()

                right = customtkinter.CTkFrame(card, fg_color="#222222")
                right.pack(side="left", fill="both", expand=True, padx=6, pady=6)
                title = c.get("title_romaji") or c.get("title_english") or "Unknown"
                year = c.get("year") or ""
                summary = c.get("description") or ""
                summary = (summary[:250] + "...") if len(summary) > 250 else summary
                genres = ", ".join(c.get("genres", []))
                staff = ", ".join(c.get("staff", []))

                title_lbl = customtkinter.CTkLabel(right, text=f"{title} ({year})", font=("Segoe UI", 12, "bold"), text_color="#ffffff")
                title_lbl.pack(fill="x", pady=(0, 6))
                summary_lbl = customtkinter.CTkLabel(right, text=summary, text_color="#cccccc", wraplength=600)
                summary_lbl.pack(fill="x", pady=(0, 6))
                genre_lbl = customtkinter.CTkLabel(right, text=f"Genres: {genres}", text_color="#aaaaaa")
                genre_lbl.pack(fill="x", pady=(0, 4))
                staff_lbl = customtkinter.CTkLabel(right, text=f"Staff: {staff}", text_color="#aaaaaa")
                staff_lbl.pack(fill="x", pady=(0, 4))

                btn = customtkinter.CTkButton(card, text="Select", width=80, command=lambda c=c: choose(c))
                btn.pack(side="right", padx=6, pady=6)

        build_candidate_cards()
        win.transient(self.root)
        win.grab_set()
        self.root.wait_window(win)
        return chosen["value"]

    def process_folder(self, folder):
        norm_name = normalize_name(guess_folder_title(folder))
        self.log_msg(f"Processing folder: {folder} (normalized: {norm_name})")

        cached = self.cache.get(norm_name)
        if cached:
            self.log_msg(f"Using cached AniList data for {norm_name}")
            metadata = cached
        else:
            self.log_msg(f"Searching AniList for: {norm_name}")
            results = anilist_search(norm_name)
            if not results:
                self.log_msg(f"No AniList results for {norm_name}")
                if messagebox.askyesno("No Match Found", f"No results for '{norm_name}'.\nDo you want to try manual URL match?"):
                    metadata = self.prompt_picker([], norm_name)
                    if not metadata:
                        self.log_msg(f"Manual match skipped for {norm_name}")
                        return
                    self.cache[norm_name] = metadata
                    save_cache(self.cache)
                else:
                    return
            else:
                metadata = self.prompt_picker(results, norm_name)
                if not metadata:
                    self.log_msg(f"No selection made for {norm_name}, skipping")
                    return
                self.cache[norm_name] = metadata
                save_cache(self.cache)

        xml_bytes = build_comicinfo(metadata, prefer=self.title_pref.get())

        cover_data = None
        if self.add_covers_var.get():  # Only download covers if toggle is ON
            cover_url = metadata.get("cover_large") or metadata.get("cover_xl")
            if cover_url:
                cover_data = download_image(cover_url)

        manga_files = [f for f in os.listdir(folder) if os.path.splitext(f)[1].lower() in FILE_EXTS]

        for file in manga_files:
            full_path = os.path.join(folder, file)
            ext = os.path.splitext(file)[1].lower()
            self.log_msg(f"Injecting into {file} ...")

            entries = {COMICINFO_NAME: xml_bytes}

            # thumbs.db detection for CBZ/CBR
            add_cover = self.add_covers_var.get()
            try:
                if add_cover:
                    if ext == ".cbz":
                        with zipfile.ZipFile(full_path, "r") as z:
                            if any(name.lower().endswith("thumbs.db") for name in z.namelist()):
                                add_cover = False
                    elif ext == ".cbr":
                        try:
                            output = subprocess.run(["7z", "l", full_path],
                                                    capture_output=True, text=True, timeout=15).stdout.lower()
                            if "thumbs.db" in output:
                                add_cover = False
                        except Exception:
                            pass
            except Exception:
                pass

            if add_cover and cover_data:
                entries[COVER_NAME] = cover_data
            elif self.add_covers_var.get() and not add_cover:
                self.log_msg(f"Skipped adding cover for {file} (contains thumbs.db)")

            if ext == ".cbz":
                success = inject_into_cbz(full_path, entries)
                if success:
                    self.log_msg(f"Injected ComicInfo.xml{' and cover.jpg' if add_cover else ''} into {file}")
                else:
                    self.log_msg(f"Failed to inject into {file}")
            elif ext == ".cbr":
                new_path = convert_cbr_to_cbz(full_path, entries)
                if new_path:
                    self.log_msg(f"Converted {file} to CBZ and injected files")
                    try:
                        self.folders.remove(folder)
                        self.update_folder_list()
                    except Exception:
                        pass
                else:
                    self.log_msg(f"Failed to convert and inject into {file}")

    def start(self):
        if not self.folders:
            messagebox.showwarning("Warning", "No folders selected")
            return
        total = len(self.folders)
        self.progress.set(0)
        self.root.update_idletasks()
        
        for i, folder in enumerate(self.folders):
            self.process_folder(folder)
            self.progress.set((i + 1) / total)
            self.root.update_idletasks()

        self.progress.set(1.0)
        messagebox.showinfo("Done", "Processing complete")

    def on_close(self):
        save_cache(self.cache)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = AniApp()
    app.run()