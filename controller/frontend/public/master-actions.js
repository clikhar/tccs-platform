(() => {
  const API = `${location.protocol}//${location.hostname}:8000`;

  async function removeMaster(path, label) {
    if (!confirm(`Remove ${label}?`)) return;
    const token = localStorage.getItem('tccs_admin_token') || '';
    try {
      const response = await fetch(API + path, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      if (typeof window.loadAll === 'function') await window.loadAll();
      if (typeof window.showMsg === 'function') window.showMsg(`${label} removed`);
    } catch (error) {
      if (typeof window.showMsg === 'function') window.showMsg(error.message, true);
      else alert(error.message);
    }
  }

  function addRemoveButtons() {
    document.querySelectorAll('#sectionRows .row').forEach(row => {
      if (row.querySelector('.master-remove-section')) return;
      const edit = row.querySelector('button[onclick^="editSection("]');
      if (!edit) return;
      const id = (edit.getAttribute('onclick').match(/\d+/) || [])[0];
      if (!id) return;
      const code = row.children[0]?.textContent?.trim() || 'section';
      const button = document.createElement('button');
      button.className = 'danger master-remove-section';
      button.textContent = 'REMOVE';
      button.addEventListener('click', () => removeMaster(`/api/v1/master/sections/${id}`, `section ${code}`));
      row.querySelector('.rowactions')?.appendChild(button);
    });

    document.querySelectorAll('#types #typeRows .row').forEach(row => {
      if (row.querySelector('.master-remove-type')) return;
      const edit = row.querySelector('button[onclick^="editType("]');
      if (!edit) return;
      const id = (edit.getAttribute('onclick').match(/\d+/) || [])[0];
      if (!id) return;
      const code = row.children[0]?.textContent?.trim() || 'station type';
      const button = document.createElement('button');
      button.className = 'danger master-remove-type';
      button.textContent = 'REMOVE';
      button.addEventListener('click', () => removeMaster(`/api/v1/master/station-types/${id}`, `station type ${code}`));
      row.querySelector('.rowactions')?.appendChild(button);
    });
  }

  window.addEventListener('load', () => {
    const originalSections = window.renderSections;
    const originalTypes = window.renderTypes;
    if (typeof originalSections === 'function') {
      window.renderSections = (...args) => {
        originalSections(...args);
        addRemoveButtons();
      };
    }
    if (typeof originalTypes === 'function') {
      window.renderTypes = (...args) => {
        originalTypes(...args);
        addRemoveButtons();
      };
    }
    addRemoveButtons();
  });
})();
