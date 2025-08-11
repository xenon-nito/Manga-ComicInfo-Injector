# 📚 Manga ComicInfo Injector

**Manga ComicInfo Injector** is a Python desktop tool for automatically fetching metadata from [AniList](https://anilist.co/) and injecting it into your `.cbz` and `.cbr` manga/comic archives in the **ComicInfo.xml** format (with optional cover image).  
This makes your manga library richer, better organized, and compatible with comic library managers like **Kavita**, **ComicRack**, and **Komga**.

<p align="center">
  <img src="https://i.imgur.com/ChWNn1v.jpeg" alt="App Preview" width="600">
</p>

> 🛠 This project was coded collaboratively with the help of **ChatGPT**.

---

## ✨ Features

- **Automatic AniList Search** – Fetches title, year, description, genres, staff, and cover art.  
- **Manual Match Option** – Paste an AniList URL if automatic search fails.  
- **ComicInfo.xml Injection** – Inserts metadata directly into `.cbz` and `.cbr` files.  
- **Cover Image Support** – Optionally adds `cover.jpg` to archives.  
- **CBR to CBZ Conversion** – Automatically converts `.cbr` files to `.cbz` if injection is needed.  
- **Folder Batch Processing** – Process single folders or entire parent directories.  
- **Caching** – Stores past matches to speed up repeated processing.  
- **GUI Interface** – Built with **CustomTkinter** for a clean dark-mode interface.

---

## 📦 Requirements

- **Python 3.9+** (earlier versions may work but are untested)  
- Install dependencies:
  ```bash
  pip install -r requirements.txt
  ```
- For `.cbr` support, ensure one of these is installed and in your system `PATH`:
  - **7-Zip** (`7z` command)

---

## 🚀 Installation & Usage

1. **Clone the repository**:
   ```bash
   git clone https://github.com/yourusername/manga-comicinfo-injector.git
   cd manga-comicinfo-injector
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the script**:
   ```bash
   python Manga_Comicinfo_Injector.py
   ```
   or use the run.bat file.

4. **Using the App**:
   - Add one or more manga folders via **Add Folder** or **Add Parent Folder**.
   - Choose **romaji** or **english** title preference.
   - Toggle **Add covers** if desired.
   - Click **Start** to fetch metadata and inject it into your files.

---
