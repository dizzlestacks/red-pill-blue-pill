fetch("videos.json")
  .then((res) => res.json())
  .then((videos) => {
    const grid = document.getElementById("videoGrid");
    const searchInput = document.getElementById("searchInput");
    const categoryFilter = document.getElementById("categoryFilter");
    const sortFilter = document.getElementById("sortFilter");

    // Extract unique categories
    const categories = [...new Set(videos.map(v => v.category).filter(Boolean))];
    categories.forEach(cat => {
      const option = document.createElement("option");
      option.value = cat;
      option.textContent = cat;
      categoryFilter.appendChild(option);
    });

    // Display videos
    function displayVideos() {
      const searchTerm = searchInput.value.toLowerCase();
      const selectedCategory = categoryFilter.value;
      const sortType = sortFilter.value;

      let filtered = [...videos];

      // Filter by tag or title
      if (searchTerm) {
        filtered = filtered.filter(v =>
          v.title.toLowerCase().includes(searchTerm) ||
          (v.tags && v.tags.some(tag => tag.toLowerCase().includes(searchTerm.replace("#", ""))))
        );
      }

      // Filter by category
      if (selectedCategory) {
        filtered = filtered.filter(v => v.category === selectedCategory);
      }

      // Sort
      if (sortType === "az") {
        filtered.sort((a, b) => a.title.localeCompare(b.title));
      } else {
        filtered.sort((a, b) => new Date(b.added) - new Date(a.added));
      }

      // Separate pinned from others
      const pinned = filtered.filter(v => v.pinned);
      const others = filtered.filter(v => !v.pinned);

      grid.innerHTML = "";

      [...pinned, ...others].forEach((video) => {
        const item = document.createElement("div");
        item.className = "video-item";

        const iframe = document.createElement("iframe");
        iframe.src = `https://www.youtube.com/embed/${video.youtube_id}?rel=0`;
        iframe.frameBorder = "0";
        iframe.allowFullscreen = true;
        iframe.loading = "lazy";

        const title = document.createElement("div");
        title.className = "video-title";
        title.textContent = video.title;

        // Share button
        const shareBtn = document.createElement("button");
        shareBtn.textContent = "Share";
        shareBtn.onclick = () => {
          const url = `${location.origin}/videos.html?video=${video.youtube_id}`;
          navigator.clipboard.writeText(url);
          alert("Video link copied!");
        };

        item.appendChild(iframe);
        item.appendChild(title);
        item.appendChild(shareBtn);
        grid.appendChild(item);
      });
    }

    // Event listeners
    searchInput.addEventListener("input", displayVideos);
    categoryFilter.addEventListener("change", displayVideos);
    sortFilter.addEventListener("change", displayVideos);

    // Initial display
    displayVideos();
  });