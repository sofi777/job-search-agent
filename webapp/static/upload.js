// Progressive enhancement for forms with file dropzones (onboarding, profile).
// Markup contract per dropzone: .dropzone[data-field] containing
//   input[type=file], [data-role=name], [data-role=check], [data-role=progress-fill] (all optional
//   except the file input).
// Wire-up: <form data-upload-form> around one or more dropzones.
(function () {
  function initDropzone(zone) {
    const input = zone.querySelector('input[type=file]');
    if (!input) return;
    const nameEl = zone.querySelector('[data-role=name]');
    const check = zone.querySelector('[data-role=check]');
    input.addEventListener('change', () => {
      if (!input.files.length) return;
      zone.classList.add('filled');
      if (nameEl) nameEl.textContent = input.files[0].name;
      if (check) check.hidden = false;
    });
  }

  function submitWithProgress(form, dropzones) {
    const submitBtn = form.querySelector('button[type=submit]');
    const originalLabel = submitBtn ? submitBtn.innerHTML : '';
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.innerHTML = '<span class="spinner"></span> Uploading…';
    }

    // XHR only reports one aggregate byte counter for the whole multipart body, so approximate
    // a per-file progress bar by giving each selected file a byte range within that total and
    // mapping the aggregate counter back onto whichever range it falls in.
    const formData = new FormData(form);
    let offset = 0;
    const ranges = [];
    dropzones.forEach((zone) => {
      const input = zone.querySelector('input[type=file]');
      const file = input && input.files[0];
      if (!file) return;
      zone.classList.add('uploading');
      ranges.push({ zone, start: offset, end: offset + file.size });
      offset += file.size;
    });

    const resetBtn = () => {
      if (!submitBtn) return;
      submitBtn.disabled = false;
      submitBtn.innerHTML = originalLabel;
    };

    const xhr = new XMLHttpRequest();
    xhr.open('POST', form.action || window.location.href);
    xhr.upload.addEventListener('progress', (e) => {
      if (!e.lengthComputable) return;
      ranges.forEach(({ zone, start, end }) => {
        const fill = zone.querySelector('[data-role=progress-fill]');
        if (!fill) return;
        const pct = end > start ? Math.min(100, Math.max(0, ((e.loaded - start) / (end - start)) * 100)) : 100;
        fill.style.width = pct + '%';
      });
    });
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 400) {
        if (xhr.responseURL && xhr.responseURL !== window.location.href) {
          window.location.href = xhr.responseURL;
        } else {
          document.open();
          document.write(xhr.responseText);
          document.close();
        }
      } else {
        resetBtn();
        alert('Upload failed, please try again.');
      }
    };
    xhr.onerror = () => {
      resetBtn();
      alert('Upload failed, please try again.');
    };
    xhr.send(formData);
  }

  function initUploadForm(form) {
    const dropzones = Array.from(form.querySelectorAll('.dropzone[data-field]'));
    dropzones.forEach(initDropzone);
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      submitWithProgress(form, dropzones);
    });
  }

  document.querySelectorAll('form[data-upload-form]').forEach(initUploadForm);
})();
