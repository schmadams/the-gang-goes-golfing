// Swipe-carousel behavior for round-post photos on the home feed
// (see _feed_round_post_card's photo_gallery in frontend/src/pages/
// home.py). The scroll/swipe itself is plain CSS (scroll-snap on
// .t3g-feed-photo-gallery -- see home.css), so this file's only job is
// the "1/6" counter badge and the dot row underneath it, since neither
// "which photo is centered right now" nor "how many photos exist after
// an upload" is something Dash's server-rendered children can react to
// on their own -- the upload callback only ever appends more <img>
// children to the track, it never touches the counter/dots.
//
// No build step, no dependency -- this app has no other JS yet, so
// kept deliberately small and vanilla rather than pulling in a
// carousel library for one feature.
(function () {
    function updateActive(track, counter, dots) {
        // Every direct child is one swipeable slide -- not just <img>
        // tags, since the auto-generated stats slide (see
        // _feed_stats_slide in home.py) is a plain <div> that swipes
        // alongside real photos in the same track.
        var count = track.children.length;
        if (!count) {
            return;
        }
        var index = Math.round(track.scrollLeft / track.clientWidth);
        index = Math.max(0, Math.min(count - 1, index));
        if (counter) {
            counter.textContent = (index + 1) + "/" + count;
        }
        if (dots) {
            Array.prototype.forEach.call(dots.children, function (dot, i) {
                dot.classList.toggle("t3g-feed-photo-dot--active", i === index);
            });
        }
    }

    function render(track, counter, dots) {
        var count = track.children.length;

        if (counter) {
            counter.style.display = count > 1 ? "block" : "none";
        }
        if (dots) {
            dots.style.display = count > 1 ? "flex" : "none";
            if (dots.childElementCount !== count) {
                dots.innerHTML = "";
                for (var i = 0; i < count; i++) {
                    var dot = document.createElement("span");
                    dot.className = "t3g-feed-photo-dot";
                    dots.appendChild(dot);
                }
            }
        }
        updateActive(track, counter, dots);
    }

    function initCarousel(track) {
        if (track.dataset.t3gCarouselBound) {
            return;
        }
        var wrapper = track.closest(".t3g-feed-photo-carousel");
        if (!wrapper) {
            return;
        }
        track.dataset.t3gCarouselBound = "1";

        var counter = wrapper.querySelector(".t3g-feed-photo-counter");
        var dots = wrapper.querySelector(".t3g-feed-photo-dots");

        track.addEventListener(
            "scroll",
            function () {
                window.requestAnimationFrame(function () {
                    updateActive(track, counter, dots);
                });
            },
            { passive: true }
        );

        // Catches both the very first paint (photos already on the
        // round) and every later upload (handle_feed_photo_upload
        // appending new <img> children server-side via Dash) -- both
        // need the counter/dot count to be recomputed the same way.
        new MutationObserver(function () {
            render(track, counter, dots);
        }).observe(track, { childList: true });

        render(track, counter, dots);
    }

    function scan() {
        document.querySelectorAll(".t3g-feed-photo-gallery").forEach(initCarousel);
    }

    document.addEventListener("DOMContentLoaded", scan);
    // Dash swaps whole page subtrees on client-side navigation (and the
    // feed re-fetches on every visit to "/"), which DOMContentLoaded
    // alone won't catch since it only ever fires once -- a body-level
    // observer re-scans for newly-mounted carousels whenever that
    // happens.
    new MutationObserver(scan).observe(document.body, { childList: true, subtree: true });
})();