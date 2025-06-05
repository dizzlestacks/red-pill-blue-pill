
README — How to Add Videos to Your Video Hub
============================================

1. WHERE TO PUT YOUR VIDEOS
---------------------------
Place all your .mp4 video files into the 'videos/' folder.
Filenames must exactly match the 'filename' field in the code.

2. HOW TO ADD OR EDIT VIDEOS
----------------------------
Open 'extra.html' using any text editor.
Scroll to the JavaScript section that starts with:

    const videos = [

Each video should be added using this format:

    {
      title: "Your Video Title",
      filename: "your-video.mp4",
      category: "Your Category",
      tags: ["tag1", "tag2", "tag3"]
    }

Example:

    {
      title: "The Truth About Time",
      filename: "truth-time.mp4",
      category: "Science",
      tags: ["time", "truth", "cosmos"]
    }

You can copy and edit one of the existing entries to add more videos.

3. HOW TAGS & CATEGORIES WORK
-----------------------------
- Category is used for the dropdown filter
- Tags are searchable via the text input (searches any tag)
- You can assign multiple tags per video

4. AFTER EDITING
----------------
Once you've added your new entries and saved extra.html, open the file in your browser.
The list will update automatically.
