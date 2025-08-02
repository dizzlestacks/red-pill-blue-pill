<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Video JSON Generator</title>
  <style>
    body {
      background: #000;
      color: #00FF41;
      font-family: monospace;
      padding: 20px;
    }
    label {
      display: block;
      margin-top: 12px;
      font-weight: bold;
    }
    input, textarea, select, button {
      width: 100%;
      font-family: monospace;
      font-size: 14px;
      margin-top: 5px;
      padding: 8px;
      background: #111;
      color: #00FF41;
      border: 1px solid #00FF41;
    }
    button {
      background: #00FF41;
      color: black;
      font-weight: bold;
      cursor: pointer;
    }
    .hint {
      font-size: 0.8em;
      color: #88ff88;
    }
  </style>
</head>
<body>

  <h1>Create New Video JSON Entry</h1>

  <label>Title:</label>
  <input id="title" placeholder="e.g. WAKE UP TO THE TRUTH" />

  <label>YouTube ID (optional):</label>
  <input id="youtube_id" placeholder="e.g. dQw4w9WgXcQ" />

  <label>Cloudflare Video ID (optional):</label>
  <input id="video_id" placeholder="e.g. ca53f3a766e01764d750d02186803f74" />

  <label>Dropbox URL (optional):</label>
  <input id="dropbox_url" placeholder="https://www.dropbox.com/s/xyz/video.mp4?raw=1" />

  <label>Local URL (optional):</label>
  <input id="url" placeholder="e.g. /videos/video.mp4" />

  <label>Category:</label>
  <input id="category" list="categoryOptions" placeholder="e.g. LAST DAYS" />
  <datalist id="categoryOptions"></datalist>

  <label>Tags (comma-separated, hashtags allowed):</label>
  <input id="tags" placeholder="e.g. #matrix, #truth, bible, scripture" />

  <label>Featured?</label>
  <select id="featured">
    <option value="false">No</option>
    <option value="true">Yes</option>
  </select>

  <label>Pinned?</label>
  <select id="pinned">
    <option value="false">No</option>
    <option value="true">Yes</option>
  </select>

  <button onclick="generateJSON()">Generate JSON</button>
  <button onclick="resetForm()">Reset</button>

  <label>Generated JSON:</label>
  <textarea id="output" readonly></textarea>
  <button onclick="copyJSON()">Copy</button>
  <button onclick="downloadJSON()">Download</button>

  <script>
    function generateJSON() {
      const entry = {
        title: document.getElementById("title").value.trim(),
        youtube_id: document.getElementById("youtube_id").value.trim() || undefined,
        video_id: document.getElementById("video_id").value.trim() || undefined,
        dropbox_url: document.getElementById("dropbox_url").value.trim() || undefined,
        url: document.getElementById("url").value.trim() || undefined,
        category: document.getElementById("category").value.trim(),
        tags: document.getElementById("tags").value.split(',').map(t => t.trim().replace(/^#/, "")).filter(Boolean),
        added: new Date().toISOString(),
        featured: document.getElementById("featured").value === "true",
        pinned: document.getElementById("pinned").value === "true"
      };

      Object.keys(entry).forEach(k => {
        if (entry[k] === "" || entry[k] === undefined) delete entry[k];
      });

      const categories = JSON.parse(localStorage.getItem("categories") || "[]");
      if (!categories.includes(entry.category)) {
        categories.push(entry.category);
        localStorage.setItem("categories", JSON.stringify(categories));
        populateCategoryOptions();
      }

      document.getElementById("output").value = JSON.stringify(entry, null, 2);
    }

    function copyJSON() {
      navigator.clipboard.writeText(document.getElementById("output").value)
        .then(() => alert("Copied!"));
    }

    function downloadJSON() {
      const blob = new Blob([document.getElementById("output").value], {type: "application/json"});
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "video.json";
      a.click();
    }

    function resetForm() {
      document.querySelectorAll("input, select, textarea").forEach(el => el.value = "");
      document.getElementById("output").value = "";
    }

    function populateCategoryOptions() {
      const datalist = document.getElementById("categoryOptions");
      datalist.innerHTML = "";
      const stored = JSON.parse(localStorage.getItem("categories") || "[]");
      stored.forEach(cat => {
        const opt = document.createElement("option");
        opt.value = cat;
        datalist.appendChild(opt);
      });
    }

    populateCategoryOptions();
  </script>

</body>
</html>