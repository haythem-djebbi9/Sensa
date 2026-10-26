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

  /* Icône SVG du gabarit #icon-templates (base.html). */
  function iconHtml(name, fallback = "") {
    const template = $("#icon-templates");
    const icon = template ? template.content.querySelector(`[data-icon="${name}"]`) : null;
    return icon ? icon.innerHTML : fallback;
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

  /* ---------- Composer : zone de texte qui grandit au fil de l'écriture ---------- */
  function initAutoGrow(textarea) {
    const grow = () => {
      textarea.style.height = "auto";
      textarea.style.height = `${textarea.scrollHeight}px`;
    };
    textarea.addEventListener("input", grow);
    grow();
  }

  /* ---------- Composer : images (carrousel) et textes alternatifs ---------- */
  function initImageInput(input) {
    const tile = input.closest(".upload-tile");
    if (!tile) return;
    const preview = $(".upload-preview img", tile);
    const count = $("[data-upload-count]", tile);
    const removeButton = $(".preview-remove", tile);
    const addButton = $("[data-image-add]", tile);
    const thumbs = $("[data-upload-thumbs]", input.form);
    const altField = document.getElementById("alt-field");
    const rows = altField ? $("[data-alt-rows]", altField) : null;
    const rowTemplate = rows ? $("[data-alt-row]", rows).cloneNode(true) : null;
    const maxFiles = Number(input.dataset.maxFiles) || 10;
    let selected = []; // images choisies, dans l'ordre du carrousel : { file, url, row }
    let shown = null; // image affichée en grand dans l'aperçu

    function showHint(row, text) {
      const hint = $("[data-alt-hint]", row);
      hint.hidden = !text;
      $("[data-alt-hint-text]", hint).textContent = text;
    }

    /* L'IA propose un texte alternatif pour une image ; l'auteur le relit avant de publier. */
    async function suggestAltText(entry) {
      const { file, row } = entry;
      const field = $("textarea", row);
      const body = new FormData();
      body.append("image", file);
      body.append("csrfmiddlewaretoken", $("input[name=csrfmiddlewaretoken]", input.form).value);
      showHint(row, "L'IA rédige une description de l'image…");
      field.setAttribute("aria-busy", "true");
      try {
        const response = await fetch(altField.dataset.suggestUrl, { method: "POST", body, headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" });
        const data = await response.json();
        if (!selected.includes(entry)) return; // image retirée entre-temps
        if (!response.ok) throw new Error(data.error || String(response.status));
        if (field.value) {
          showHint(row, ""); // l'auteur a commencé à écrire le sien
        } else if (data.approximate) {
          field.placeholder = data.alt_text;
          showHint(row, "Description approximative (IA non configurée) : décrivez l'image vous-même.");
        } else {
          field.value = data.alt_text;
          showHint(row, "Suggestion de l'IA : relisez-la et corrigez-la si besoin.");
        }
      } catch (error) {
        if (selected.includes(entry)) showHint(row, "Suggestion indisponible : vous pouvez décrire l'image vous-même.");
      } finally {
        field.removeAttribute("aria-busy");
      }
    }

    /* Met à jour le champ de fichiers, l'aperçu et les lignes après chaque ajout, retrait ou déplacement. */
    function sync() {
      const transfer = new DataTransfer();
      selected.forEach(({ file }) => transfer.items.add(file));
      input.files = transfer.files;
      const total = selected.length;
      tile.classList.toggle("has-file", total > 0);
      if (count) count.hidden = total < 2;
      if (addButton) addButton.hidden = total >= maxFiles;
      if (thumbs) thumbs.hidden = total < 2;
      if (!total) {
        shown = null;
        if (thumbs) thumbs.replaceChildren();
        if (altField) {
          altField.hidden = true;
          rows.replaceChildren(rowTemplate.cloneNode(true));
        }
        return;
      }
      if (!selected.includes(shown)) shown = selected[0];
      const shownPosition = selected.indexOf(shown) + 1;
      preview.src = shown.url;
      preview.alt = total > 1 ? `Aperçu de l'image ${shownPosition} sur ${total}` : `Aperçu de ${shown.file.name}`;
      if (count) count.textContent = `${shownPosition}/${total}`;
      if (thumbs) thumbs.replaceChildren(...selected.map(thumbItem));
      if (!altField) return;
      altField.hidden = false;
      rows.replaceChildren(...selected.map(({ row }) => row));
      selected.forEach(({ row }, index) => {
        const position = index + 1;
        $("[data-alt-label]", row).textContent = total > 1 ? `Image ${position}` : "Texte alternatif";
        $("[data-alt-thumb]", row).alt = `Image ${position}`;
        const [up, down, remove] = $$("[data-alt-actions] button", row);
        up.disabled = index === 0;
        down.disabled = index === total - 1;
        up.hidden = down.hidden = total < 2;
        up.setAttribute("aria-label", `Avancer l'image ${position}`);
        down.setAttribute("aria-label", `Reculer l'image ${position}`);
        remove.setAttribute("aria-label", `Retirer l'image ${position}`);
      });
    }

    function removeEntry(entry) {
      selected = selected.filter((item) => item !== entry);
      URL.revokeObjectURL(entry.url);
      sync();
    }

    /* Vignette de la rangée sous l'aperçu : la toucher l'affiche en grand, × la retire. */
    function thumbItem(entry, index) {
      const position = index + 1;
      const item = document.createElement("li");
      item.className = "upload-thumb";
      item.innerHTML = '<button type="button" class="thumb-show"><img alt=""></button><span class="thumb-number" aria-hidden="true"></span><button type="button" class="thumb-remove"></button>';
      const [show, remove] = $$("button", item);
      $("img", show).src = entry.url;
      $(".thumb-number", item).textContent = position;
      show.setAttribute("aria-label", `Afficher l'image ${position}`);
      show.setAttribute("aria-pressed", String(entry === shown));
      remove.setAttribute("aria-label", `Retirer l'image ${position}`);
      remove.innerHTML = iconHtml("x", "×");
      show.addEventListener("click", () => {
        shown = entry;
        sync();
        $$(".thumb-show", thumbs)[index].focus();
      });
      remove.addEventListener("click", () => {
        removeEntry(entry);
        const next = $$(".thumb-remove", thumbs)[Math.min(index, selected.length - 1)];
        (thumbs.hidden || !next ? addButton || input : next).focus();
      });
      return item;
    }

    function addFile(file) {
      const entry = { file, url: URL.createObjectURL(file), row: rowTemplate.cloneNode(true) };
      const thumb = $("[data-alt-thumb]", entry.row);
      thumb.src = entry.url;
      thumb.hidden = false;
      $("textarea", entry.row).value = "";
      $("textarea", entry.row).addEventListener("input", () => showHint(entry.row, ""));
      $("[data-alt-actions]", entry.row).hidden = false;
      $$("[data-move]", entry.row).forEach((button) => button.addEventListener("click", () => {
        const from = selected.indexOf(entry);
        const to = from + Number(button.dataset.move);
        selected.splice(from, 1);
        selected.splice(to, 0, entry);
        sync();
        button.focus();
      }));
      $("[data-remove-image]", entry.row).addEventListener("click", () => removeEntry(entry));
      selected.push(entry);
      if (altField && altField.dataset.suggestUrl) suggestAltText(entry);
    }

    /* Ajoute des images choisies avec le bouton ou déposées sur le formulaire. */
    function addFiles(files) {
      if (!files.length) return;
      let problem = "";
      if (selected.length + files.length > maxFiles) problem = `${maxFiles} images maximum par publication.`;
      else if (files.some((file) => file.size > 10 * 1024 * 1024)) problem = "Une image dépasse 10 Mo.";
      else if (files.some((file) => !["image/jpeg", "image/png", "image/webp"].includes(file.type))) problem = "Formats acceptés : JPEG, PNG ou WebP.";
      if (problem) {
        showToast(problem, "error");
        sync();
        return;
      }
      files.forEach(addFile);
      sync();
    }

    // Le navigateur ne garde que le dernier choix : on l'ajoute aux images déjà sélectionnées.
    input.addEventListener("change", () => addFiles(Array.from(input.files || [])));

    // Glisser-déposer des fichiers n'importe où sur le formulaire ; la zone des images s'illumine.
    let dragDepth = 0;
    const carriesFiles = (event) => Array.from(event.dataTransfer ? event.dataTransfer.types : []).includes("Files");
    const endDrag = () => { dragDepth = 0; tile.classList.remove("is-dragover"); };
    input.form.addEventListener("dragenter", (event) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      dragDepth += 1;
      tile.classList.add("is-dragover");
    });
    input.form.addEventListener("dragover", (event) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    });
    input.form.addEventListener("dragleave", (event) => {
      if (carriesFiles(event) && --dragDepth <= 0) endDrag();
    });
    input.form.addEventListener("drop", (event) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      endDrag();
      addFiles(Array.from(event.dataTransfer.files));
    });

    if (addButton) addButton.addEventListener("click", (event) => { event.preventDefault(); input.click(); });
    if (removeButton) {
      removeButton.addEventListener("click", (event) => {
        event.preventDefault();
        selected.forEach(({ url }) => URL.revokeObjectURL(url));
        selected = [];
        sync();
      });
    }
  }

  /* ---------- Carrousel d'images : souris, doigt, clavier (← →) et lecteur d'écran ---------- */
  function initCarousel(carousel) {
    if (carousel.dataset.ready) return;
    carousel.dataset.ready = "1";
    const track = $("[data-carousel-track]", carousel);
    const counter = $("[data-carousel-count]", carousel);
    const status = $("[data-carousel-status]", carousel);
    const prev = $("[data-carousel-prev]", carousel);
    const next = $("[data-carousel-next]", carousel);
    const slides = Array.from(track.children);
    const total = slides.length;
    const current = () => Math.round(track.scrollLeft / track.clientWidth);
    let target = 0; // image demandée (le défilement doux met un moment à l'atteindre)
    let shown = 0;
    let settleTimer = null;

    const update = () => {
      const index = current();
      const focused = document.activeElement;
      if (counter) counter.textContent = `${index + 1}/${total}`;
      if (prev) prev.disabled = index === 0;
      if (next) next.disabled = index >= total - 1;
      // Un bouton désactivé perd le focus : on le donne à l'autre bouton.
      if (focused === prev && prev.disabled) next.focus();
      if (focused === next && next.disabled) prev.focus();
      // Seule l'image visible est atteignable avec Tab ; les autres se parcourent avec les flèches.
      slides.forEach((slide, position) => {
        if (position === index) slide.removeAttribute("tabindex");
        else if (slide !== document.activeElement) slide.setAttribute("tabindex", "-1");
      });
      if (index !== shown) {
        shown = index;
        // Annoncée par le lecteur d'écran, sauf si le focus est déjà sur l'image (son nom est alors lu).
        if (status && !track.contains(document.activeElement)) status.textContent = `Image ${index + 1} sur ${total}`;
      }
    };

    const go = (step, moveFocus = false) => {
      target = Math.min(Math.max(target + step, 0), total - 1);
      const slide = slides[target];
      if (moveFocus) {
        slide.removeAttribute("tabindex");
        slide.focus({ preventScroll: true });
      }
      track.scrollTo({ left: slide.offsetLeft - track.offsetLeft });
    };

    if (prev) prev.addEventListener("click", () => go(-1));
    if (next) next.addEventListener("click", () => go(1));
    carousel.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      go(event.key === "ArrowRight" ? 1 : -1, track.contains(document.activeElement));
    });
    track.addEventListener("scroll", () => {
      window.requestAnimationFrame(update);
      clearTimeout(settleTimer);
      settleTimer = setTimeout(() => { target = current(); }, 150); // après un glissement au doigt
    }, { passive: true });
    update();
  }

  /* ---------- Images : apparition en fondu une fois chargées ---------- */
  function initFadeIn(img) {
    if (img.complete) return; // déjà affichée : pas de clignotement
    img.classList.add("fade-pending");
    const show = () => img.classList.remove("fade-pending");
    img.addEventListener("load", show, { once: true });
    img.addEventListener("error", show, { once: true });
  }

  /* ---------- Double-tap sur une photo : j'aime ---------- */
  /* Un toucher garde son action (ouvrir la publication, agrandir l'image) ;
     deux touchers rapprochés aiment la publication, avec un cœur sur la photo. */
  function initDoubleTap(media, likeForm, onSingle) {
    if (media.dataset.tapReady) return;
    media.dataset.tapReady = "1";
    let timer = null;
    media.addEventListener("click", (event) => {
      // Ctrl/⌘-clic, clic milieu : comportement normal du lien (nouvel onglet…).
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      // Clavier (Entrée) ou pas de bouton J'aime : action immédiate.
      if (event.detail === 0 || !likeForm) { onSingle(); return; }
      if (timer) {
        clearTimeout(timer);
        timer = null;
        likeFromPhoto(media, likeForm);
        return;
      }
      timer = setTimeout(() => { timer = null; onSingle(); }, 260);
    });
  }

  function likeFromPhoto(media, likeForm) {
    const frame = $(".media-frame", media) || media;
    const burst = document.createElement("span");
    burst.className = "like-burst";
    burst.setAttribute("aria-hidden", "true");
    burst.innerHTML = iconHtml("heart", "♥");
    frame.appendChild(burst);
    setTimeout(() => burst.remove(), 900);
    // Comme sur les réseaux sociaux, le double-tap aime mais n'annule jamais un j'aime.
    if ($("button", likeForm).getAttribute("aria-pressed") !== "true") likeForm.requestSubmit();
  }

  function initFeedMedia(media) {
    const card = media.closest(".post-card");
    initDoubleTap(media, card ? $("form[data-like-form]", card) : null, () => { window.location.href = media.href; });
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
        if (data.liked) {
          button.classList.remove("like-pop");
          void button.offsetWidth; // relance l'animation du cœur
          button.classList.add("like-pop");
        }
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
    $$(".media-frame img", root).forEach(initFadeIn);
    $$(".post-card .media-wrap", root).forEach(initFeedMedia);
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
    $$(".composer textarea[data-char-source]").forEach(initAutoGrow);
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
