/* Sensa — amélioration progressive des gabarits Django.
   Tout fonctionne sans JavaScript (formulaires classiques) ; ce script ajoute
   le lecteur audio, l'enregistrement vocal, les likes sans rechargement,
   le rafraîchissement des posts en cours d'analyse et les notifications temps réel. */
(function () {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const MAX_AUDIO_SECONDS = 180;

  function formatTime(seconds) {
    const total = Math.max(0, Math.round(seconds || 0));
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  }

  function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  /* ---------- Messages (toasts) ---------- */
  function messagesBox() {
    let box = $(".messages");
    if (!box) {
      box = document.createElement("div");
      box.className = "messages";
      box.setAttribute("aria-live", "polite");
      document.body.appendChild(box);
    }
    return box;
  }

  function closeToast(toast) {
    toast.style.transition = "opacity 200ms";
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 220);
  }

  function initToast(toast) {
    const close = $(".toast-close", toast);
    if (close) close.addEventListener("click", () => closeToast(toast));
    setTimeout(() => toast.isConnected && closeToast(toast), 7000);
  }

  function showToast(html, level = "info") {
    const icons = { success: "check", info: "bell", warning: "alert", error: "alert" };
    const template = $("#icon-templates");
    const icon = template ? template.content.querySelector(`[data-icon="${icons[level] || "bell"}"]`) : null;
    const toast = document.createElement("div");
    toast.className = `toast toast-${level}`;
    toast.setAttribute("role", "status");
    toast.innerHTML = `${icon ? icon.outerHTML : ""}<p>${html}</p><button type="button" class="toast-close" aria-label="Fermer">×</button>`;
    messagesBox().appendChild(toast);
    initToast(toast);
  }

  /* ---------- Lecteur audio ---------- */
  let currentAudio = null;

  function initAudioPlayer(player) {
    if (player.dataset.ready) return;
    player.dataset.ready = "1";
    const audio = $("audio", player);
    const toggle = $("[data-audio-toggle]", player);
    const bars = $$(".wave-bar", player);
    const timeLabel = $("[data-audio-time]", player);
    const stateLabel = $("[data-audio-state]", player);
    const waveform = $(".waveform", player);
    const transcript = player.dataset.transcript ? document.getElementById(player.dataset.transcript) : null;
    if (!audio || !toggle) return;
    const playIcon = toggle.innerHTML;
    const pauseIcon = $("#icon-templates") ? $("#icon-templates").content.querySelector('[data-icon="pause"]').outerHTML : "❚❚";
    const idleLabel = stateLabel ? stateLabel.textContent : "";

    function render() {
      const duration = audio.duration && isFinite(audio.duration) ? audio.duration : Number(player.dataset.duration || 0);
      const ratio = duration ? audio.currentTime / duration : 0;
      const active = Math.round(ratio * bars.length);
      bars.forEach((bar, index) => bar.classList.toggle("wave-active", index < active));
      if (timeLabel) timeLabel.textContent = audio.currentTime > 0 ? `${formatTime(audio.currentTime)} / ${formatTime(duration)}` : formatTime(duration);
      if (transcript) {
        const lines = $$("[data-start]", transcript);
        let current = null;
        lines.forEach((line) => { if (Number(line.dataset.start) <= audio.currentTime + 0.2) current = line; });
        lines.forEach((line) => line.classList.toggle("is-current", line === current && !audio.paused));
      }
    }

    toggle.addEventListener("click", () => {
      if (audio.paused) {
        if (currentAudio && currentAudio !== audio) currentAudio.pause();
        audio.play().catch(() => showToast("Lecture audio impossible dans ce navigateur.", "error"));
      } else {
        audio.pause();
      }
    });
    audio.addEventListener("play", () => {
      currentAudio = audio;
      toggle.innerHTML = pauseIcon;
      toggle.setAttribute("aria-label", "Mettre en pause");
      if (stateLabel) stateLabel.textContent = "Lecture";
    });
    audio.addEventListener("pause", () => {
      toggle.innerHTML = playIcon;
      toggle.setAttribute("aria-label", "Lire l'audio");
      if (stateLabel) stateLabel.textContent = idleLabel;
      render();
    });
    audio.addEventListener("ended", () => { audio.currentTime = 0; render(); });
    audio.addEventListener("timeupdate", render);
    audio.addEventListener("loadedmetadata", render);
    if (waveform) {
      waveform.addEventListener("click", (event) => {
        const rect = waveform.getBoundingClientRect();
        const duration = audio.duration && isFinite(audio.duration) ? audio.duration : Number(player.dataset.duration || 0);
        if (!duration) return;
        audio.currentTime = ((event.clientX - rect.left) / rect.width) * duration;
        render();
      });
    }
    if (transcript) {
      $$("[data-start] button", transcript).forEach((button) => {
        button.addEventListener("click", () => {
          audio.currentTime = Number(button.closest("[data-start]").dataset.start);
          if (audio.paused) toggle.click();
        });
      });
    }
  }

  /* ---------- Interrupteurs (transcription) ---------- */
  function initToggle(button) {
    if (button.dataset.ready) return;
    button.dataset.ready = "1";
    const target = document.getElementById(button.dataset.toggleTarget);
    if (!target) return;
    button.addEventListener("click", () => {
      const show = target.hidden;
      target.hidden = !show;
      button.classList.toggle("toggle-on", show);
      button.setAttribute("aria-pressed", String(show));
    });
  }

  /* ---------- Compteur de caractères ---------- */
  function initCharCount(textarea) {
    const counter = textarea.form ? $("[data-char-count]", textarea.form) : null;
    if (!counter) return;
    const max = Number(textarea.getAttribute("maxlength") || 2000);
    const update = () => {
      counter.textContent = `${textarea.value.length} / ${max}`;
      counter.classList.toggle("near-limit", textarea.value.length > max * 0.9);
    };
    textarea.addEventListener("input", update);
    update();
  }

  /* ---------- Composer : images (carrousel) ---------- */
  function initImageInput(input) {
    const tile = input.closest(".upload-tile");
    if (!tile) return;
    const preview = $(".upload-preview img", tile);
    const count = $("[data-upload-count]", tile);
    const removeButton = $(".preview-remove", tile);
    const altField = document.getElementById("alt-field");
    const altInput = altField ? $("input", altField) : null;
    const hint = altField ? $("[data-alt-hint]", altField) : null;
    const maxFiles = Number(input.dataset.maxFiles) || 10;
    let suggestion = "";
    let suggestionRequest = 0;

    function showHint(text) {
      if (!hint) return;
      hint.hidden = !text;
      $("[data-alt-hint-text]", hint).textContent = text;
    }

    /* L'IA propose un texte alternatif pour la première image ; l'auteur le relit avant de publier. */
    async function suggestAltText(file) {
      const url = altField && altField.dataset.suggestUrl;
      if (!url || !altInput) return;
      if (altInput.value && altInput.value !== suggestion) return; // l'auteur a déjà écrit le sien
      const request = ++suggestionRequest;
      const body = new FormData();
      body.append("image", file);
      body.append("csrfmiddlewaretoken", $("input[name=csrfmiddlewaretoken]", input.form).value);
      showHint("L'IA rédige une description de l'image…");
      altInput.setAttribute("aria-busy", "true");
      try {
        const response = await fetch(url, { method: "POST", body, headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" });
        const data = await response.json();
        if (request !== suggestionRequest) return; // une autre image a été choisie entre-temps
        if (!response.ok) throw new Error(data.error || String(response.status));
        if (altInput.value && altInput.value !== suggestion) {
          showHint("");
        } else if (data.approximate) {
          altInput.value = suggestion = "";
          altInput.placeholder = data.alt_text;
          showHint("Description approximative (IA non configurée) : décrivez l'image vous-même.");
        } else {
          altInput.value = suggestion = data.alt_text;
          showHint("Suggestion de l'IA : relisez-la et corrigez-la si besoin.");
        }
      } catch (error) {
        if (request === suggestionRequest) showHint("Suggestion indisponible : vous pouvez décrire l'image vous-même.");
      } finally {
        if (request === suggestionRequest) altInput.removeAttribute("aria-busy");
      }
    }
    input.addEventListener("change", () => {
      const files = Array.from(input.files || []);
      if (!files.length) return;
      let problem = "";
      if (files.length > maxFiles) problem = `${maxFiles} images maximum par publication.`;
      else if (files.some((file) => file.size > 10 * 1024 * 1024)) problem = "Une image dépasse 10 Mo.";
      else if (files.some((file) => !["image/jpeg", "image/png", "image/webp"].includes(file.type))) problem = "Formats acceptés : JPEG, PNG ou WebP.";
      if (problem) {
        showToast(problem, "error");
        input.value = "";
        return;
      }
      preview.src = URL.createObjectURL(files[0]);
      preview.alt = files.length > 1 ? `Aperçu de la première des ${files.length} images` : `Aperçu de ${files[0].name}`;
      if (count) {
        count.hidden = files.length < 2;
        count.textContent = `${files.length} images`;
      }
      if (altInput) altInput.placeholder = files.length > 1 ? "Décrivez la première image — l'IA décrit les autres" : "Décrivez l'image — sinon l'IA s'en charge";
      tile.classList.add("has-file");
      if (altField) altField.hidden = false;
      suggestAltText(files[0]);
    });
    if (removeButton) {
      removeButton.addEventListener("click", (event) => {
        event.preventDefault();
        input.value = "";
        if (count) count.hidden = true;
        tile.classList.remove("has-file");
        if (altField) altField.hidden = true;
        suggestionRequest += 1;
        if (altInput && altInput.value === suggestion) altInput.value = suggestion = "";
        showHint("");
      });
    }
  }

  /* ---------- Carrousel d'images ---------- */
  function initCarousel(carousel) {
    if (carousel.dataset.ready) return;
    carousel.dataset.ready = "1";
    const track = $("[data-carousel-track]", carousel);
    const counter = $("[data-carousel-count]", carousel);
    const prev = $("[data-carousel-prev]", carousel);
    const next = $("[data-carousel-next]", carousel);
    const total = track.children.length;
    const current = () => Math.round(track.scrollLeft / track.clientWidth);
    const update = () => {
      const index = current();
      if (counter) counter.textContent = `${index + 1}/${total}`;
      if (prev) prev.disabled = index === 0;
      if (next) next.disabled = index >= total - 1;
    };
    const go = (step) => {
      const slide = track.children[Math.min(Math.max(current() + step, 0), total - 1)];
      track.scrollTo({ left: slide.offsetLeft - track.offsetLeft });
    };
    if (prev) prev.addEventListener("click", () => go(-1));
    if (next) next.addEventListener("click", () => go(1));
    track.addEventListener("scroll", () => window.requestAnimationFrame(update), { passive: true });
    update();
  }

  /* ---------- Composer : enregistrement audio dans le navigateur ---------- */
  function initRecorder(tile) {
    const button = $("[data-record-toggle]", tile);
    const input = $("[data-audio-input]", tile.closest("form"));
    const title = $("[data-record-title]", tile);
    const hint = $("[data-record-hint]", tile);
    const icon = $(".record-icon", tile);
    const preview = $("audio", tile);
    const removeButton = $(".audio-remove", tile);
    const importLink = $("[data-audio-import]", tile);
    const templates = $("#icon-templates");
    const micIcon = icon ? icon.innerHTML : "";
    const stopIcon = templates ? templates.content.querySelector('[data-icon="stop"]').outerHTML : "■";
    let recorder = null, chunks = [], startedAt = 0, timer = null, stream = null;

    function setIdle(label, detail) {
      tile.classList.remove("is-recording");
      if (icon) icon.innerHTML = micIcon;
      if (title) title.textContent = label;
      if (hint) hint.textContent = detail;
    }

    function attachFile(file, seconds) {
      const transfer = new DataTransfer();
      transfer.items.add(file);
      input.files = transfer.files;
      preview.src = URL.createObjectURL(file);
      preview.hidden = false;
      tile.classList.add("has-audio");
      setIdle("Audio prêt", `${formatTime(seconds)} · prêt à publier`);
    }

    function clearAudio() {
      input.value = "";
      preview.hidden = true;
      preview.removeAttribute("src");
      tile.classList.remove("has-audio");
      setIdle("Enregistrer un audio", "3 minutes maximum");
    }

    async function start() {
      if (!navigator.mediaDevices || !window.MediaRecorder) {
        showToast("L'enregistrement n'est pas pris en charge : importez plutôt un fichier audio.", "warning");
        input.click();
        return;
      }
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (error) {
        showToast("Accès au micro refusé. Autorisez le micro ou importez un fichier.", "error");
        return;
      }
      const types = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
      const mimeType = types.find((type) => MediaRecorder.isTypeSupported(type)) || "";
      recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      chunks = [];
      recorder.addEventListener("dataavailable", (event) => event.data.size && chunks.push(event.data));
      recorder.addEventListener("stop", () => {
        stream.getTracks().forEach((track) => track.stop());
        clearInterval(timer);
        const seconds = Math.min((Date.now() - startedAt) / 1000, MAX_AUDIO_SECONDS);
        const type = recorder.mimeType || "audio/webm";
        const extension = type.includes("ogg") ? "ogg" : type.includes("mp4") ? "m4a" : "webm";
        const file = new File(chunks, `enregistrement-${Date.now()}.${extension}`, { type: type.split(";")[0] });
        attachFile(file, seconds);
      });
      recorder.start(250);
      startedAt = Date.now();
      tile.classList.add("is-recording");
      if (icon) icon.innerHTML = stopIcon;
      if (hint) hint.textContent = "Touchez pour terminer";
      const tick = () => {
        const elapsed = (Date.now() - startedAt) / 1000;
        if (title) title.textContent = `Enregistrement · ${formatTime(elapsed)}`;
        if (elapsed >= MAX_AUDIO_SECONDS) {
          stop();
          showToast("Durée maximale de 3 minutes atteinte.", "info");
        }
      };
      tick();
      timer = setInterval(tick, 250);
    }

    function stop() {
      if (recorder && recorder.state !== "inactive") recorder.stop();
    }

    button.addEventListener("click", () => (recorder && recorder.state === "recording" ? stop() : start()));
    if (removeButton) removeButton.addEventListener("click", (event) => { event.preventDefault(); clearAudio(); });
    if (importLink) importLink.addEventListener("click", (event) => { event.preventDefault(); input.click(); });
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      if (!file || (recorder && recorder.state === "recording")) return;
      if (file.size > 10 * 1024 * 1024) {
        showToast("L'audio dépasse 10 Mo.", "error");
        clearAudio();
        return;
      }
      const probe = new Audio(URL.createObjectURL(file));
      probe.addEventListener("loadedmetadata", () => {
        const seconds = isFinite(probe.duration) ? probe.duration : 0;
        if (seconds > MAX_AUDIO_SECONDS + 1) {
          showToast("L'audio ne doit pas dépasser 3 minutes.", "error");
          clearAudio();
          return;
        }
        preview.src = probe.src;
        preview.hidden = false;
        tile.classList.add("has-audio");
        setIdle("Audio importé", `${seconds ? formatTime(seconds) : "Durée inconnue"} · ${file.name}`);
      });
    });
  }

  /* ---------- Likes et abonnements sans rechargement ---------- */
  async function postForm(form) {
    const response = await fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: { "X-Requested-With": "fetch" },
      credentials: "same-origin",
    });
    if (!response.ok) throw new Error(String(response.status));
    return response.json();
  }

  function initLikeForm(form) {
    if (form.dataset.ready) return;
    form.dataset.ready = "1";
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = $("button", form);
      try {
        const data = await postForm(form);
        button.classList.toggle("liked", data.liked);
        button.setAttribute("aria-pressed", String(data.liked));
        const count = $("[data-like-count]", button);
        if (count) count.textContent = data.count;
      } catch (error) {
        form.submit();
      }
    });
  }

  /* Bouton bascule (republier, enregistrer) : `key` est le booléen renvoyé par le serveur. */
  function initToggleForm(form, key, className, onMessage, offMessage) {
    if (form.dataset.ready) return;
    form.dataset.ready = "1";
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = $("button", form);
      try {
        const data = await postForm(form);
        button.classList.toggle(className, data[key]);
        button.setAttribute("aria-pressed", String(data[key]));
        const count = $("[data-repost-count]", button);
        if (count && "count" in data) count.textContent = data.count;
        showToast(data[key] ? onMessage : offMessage, "info");
      } catch (error) {
        form.submit();
      }
    });
  }

  function initRepostForm(form) {
    initToggleForm(form, "republished", "republished", "Publication republiée sur votre profil.", "Republication annulée.");
  }

  function initSaveForm(form) {
    initToggleForm(form, "saved", "saved", "Publication enregistrée.", "Publication retirée des enregistrements.");
  }

  function initFollowForm(form) {
    if (form.dataset.ready) return;
    form.dataset.ready = "1";
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = $("button", form);
      try {
        const data = await postForm(form);
        button.textContent = data.following ? "Abonné·e" : "Suivre";
        button.classList.toggle("btn-secondary", data.following);
        button.classList.toggle("btn-primary", !data.following);
        const counter = document.querySelector("[data-followers-count]");
        if (counter) counter.textContent = data.followers;
      } catch (error) {
        form.submit();
      }
    });
  }

  /* ---------- Partage ---------- */
  function initShare(button) {
    if (button.dataset.ready) return;
    button.dataset.ready = "1";
    button.addEventListener("click", async () => {
      const url = new URL(button.dataset.shareUrl, window.location.origin).href;
      try {
        if (navigator.share) {
          await navigator.share({ title: "Sensa", url });
        } else {
          await navigator.clipboard.writeText(url);
          showToast("Lien copié dans le presse-papiers.", "success");
        }
      } catch (error) { /* partage annulé */ }
    });
  }

  /* ---------- Confirmation avant action irréversible ---------- */
  function initConfirm(form) {
    if (form.dataset.ready) return;
    form.dataset.ready = "1";
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  }

  /* ---------- Posts en cours d'analyse : rafraîchissement automatique ---------- */
  async function refreshCard(card) {
    const response = await fetch(card.dataset.cardUrl, { headers: { "X-Requested-With": "fetch" } });
    if (!response.ok) return null;
    const status = response.headers.get("X-Post-Status");
    const wrapper = document.createElement("div");
    wrapper.innerHTML = (await response.text()).trim();
    const fresh = wrapper.firstElementChild;
    if (fresh && card.isConnected) {
      card.replaceWith(fresh);
      enhance(fresh);
    }
    return status;
  }

  /* Progression du pipeline poussée par le serveur (module 3). */
  function onPipelineEvent(data) {
    const card = document.getElementById(`post-${data.publication}`);
    if (!card) return;
    const step = Math.min((data.done || 0) + 1, data.total || 1);
    if (card.matches("[data-detail-post]")) {
      const label = card.querySelector("[data-pipeline-label]");
      if (data.status !== "PENDING") {
        if (!card.dataset.finalNotified) {
          card.dataset.finalNotified = "1";
          showToast(`Analyse IA terminée. <a href="${window.location.pathname}">Afficher les enrichissements</a>`, "success");
        }
      } else if (label) {
        label.textContent = `L'IA analyse cette publication… étape ${step}/${data.total}${data.step ? " · " + data.step : ""}`;
      }
      return;
    }
    if (data.status !== "PENDING") {
      if (card.dataset.cardUrl) refreshCard(card).catch(() => {});
      return;
    }
    const box = card.querySelector("[data-pipeline-progress]");
    if (box) {
      const bar = box.querySelector(".pipeline-bar span");
      if (bar) bar.style.width = `${data.percent}%`;
      const label = box.querySelector("[data-pipeline-label]");
      if (label) label.textContent = `Étape ${step}/${data.total}${data.step ? " · " + data.step + "…" : ""}${data.retry ? " (nouvelle tentative programmée)" : ""}`;
    }
  }

  function watchPendingPost(card) {
    if (card.dataset.watching) return;
    card.dataset.watching = "1";
    let attempts = 0;
    const poll = async () => {
      attempts += 1;
      if (attempts > 40 || !card.isConnected) return;
      try {
        const head = await fetch(card.dataset.cardUrl, { method: "HEAD", headers: { "X-Requested-With": "fetch" } });
        if (head.ok && head.headers.get("X-Post-Status") !== "PENDING") {
          await refreshCard(card);
          return;
        }
      } catch (error) { /* réseau indisponible : on réessaie */ }
      setTimeout(poll, Math.min(3000 + attempts * 500, 10000));
    };
    setTimeout(poll, 3000);
  }

  /* ---------- Notifications temps réel (WebSocket Django Channels) ---------- */
  function setUnread(count) {
    $$("[data-notif-count]").forEach((badge) => {
      badge.textContent = count > 99 ? "99+" : String(count);
      badge.hidden = !count;
    });
  }

  function startNotifications() {
    const url = document.body.dataset.wsUrl;
    if (!url) return;
    let failures = 0;
    let pollTimer = null;

    const poll = () => {
      if (pollTimer) return;
      pollTimer = setInterval(async () => {
        try {
          const response = await fetch(document.body.dataset.countUrl, { headers: { "X-Requested-With": "fetch" } });
          if (response.ok) setUnread((await response.json()).unread);
        } catch (error) { /* hors ligne */ }
      }, 30000);
    };

    const connect = () => {
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      const socket = new WebSocket(`${scheme}://${window.location.host}${url}`);
      socket.addEventListener("open", () => { failures = 0; });
      socket.addEventListener("message", (event) => {
        const data = JSON.parse(event.data);
        if (typeof data.unread === "number") setUnread(data.unread);
        if (data.type === "pipeline") onPipelineEvent(data);
        if (data.type === "notification") {
          const message = data.message ? ` — ${escapeHtml(data.message)}` : "";
          showToast(`<strong>${escapeHtml(data.title)}</strong>${message} <a href="${escapeHtml(data.url)}">Voir</a>`, "info");
        }
      });
      socket.addEventListener("close", () => {
        failures += 1;
        if (failures >= 3) { poll(); return; }
        setTimeout(connect, 2000 * failures);
      });
    };
    connect();
  }

  /* ---------- Curseurs (seuils IA) ---------- */
  function initRange(input) {
    const output = document.getElementById(input.dataset.output);
    if (!output) return;
    const update = () => {
      output.textContent = `${input.value} %`;
      const scale = document.querySelector("[data-threshold-scale]");
      const review = document.querySelector("[data-threshold='review']");
      const block = document.querySelector("[data-threshold='block']");
      if (scale && review && block) {
        const [low, mid, high] = scale.children;
        const r = Number(review.value), b = Math.max(Number(block.value), r);
        low.style.flex = String(r);
        mid.style.flex = String(b - r);
        high.style.flex = String(100 - b);
      }
    };
    input.addEventListener("input", update);
    update();
  }

  /* ---------- Initialisation ---------- */
  function enhance(root) {
    $$("[data-audio-player]", root).forEach(initAudioPlayer);
    $$("[data-carousel]", root).forEach(initCarousel);
    $$("[data-toggle-target]", root).forEach(initToggle);
    $$("form[data-like-form]", root).forEach(initLikeForm);
    $$("form[data-repost-form]", root).forEach(initRepostForm);
    $$("form[data-save-form]", root).forEach(initSaveForm);
    $$("form[data-follow-form]", root).forEach(initFollowForm);
    $$("[data-share-url]", root).forEach(initShare);
    $$("form[data-confirm]", root).forEach(initConfirm);
    $$("[data-status='PENDING'][data-card-url]", root).forEach(watchPendingPost);
    if (root.matches && root.matches("[data-status='PENDING'][data-card-url]")) watchPendingPost(root);
  }

  document.addEventListener("DOMContentLoaded", () => {
    $$(".toast").forEach(initToast);
    $$("textarea[data-char-source]").forEach(initCharCount);
    $$("[data-image-input]").forEach(initImageInput);
    $$("[data-recorder]").forEach(initRecorder);
    $$("input[type=range][data-output]").forEach(initRange);
    $$("[data-autosubmit]").forEach((field) => field.addEventListener("change", () => field.form && field.form.submit()));
    enhance(document);
    startNotifications();

    // Ferme les menus contextuels au clic extérieur.
    document.addEventListener("click", (event) => {
      $$("details.menu[open]").forEach((menu) => { if (!menu.contains(event.target)) menu.open = false; });
    });

    // ⌘K / Ctrl+K : recherche.
    document.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        const field = $("#search-input");
        if (field) field.focus();
        else if (document.body.dataset.searchUrl) window.location.href = document.body.dataset.searchUrl;
      }
    });
  });
})();
