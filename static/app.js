// Global variables
let progressPollingInterval = null;
let currentProgressUsername = null;

// Toast notification system
function showToast(message, type = 'info', duration = 5000) {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;

    const icons = {
        'success': '✅',
        'error': '❌',
        'warning': '⚠️',
        'info': 'ℹ️'
    };

    toast.innerHTML = `
        <div class="toast-content">
            <span class="toast-icon">${icons[type] || icons.info}</span>
            <span class="toast-message">${message}</span>
            <button class="toast-close" onclick="this.parentElement.parentElement.remove()">×</button>
        </div>
    `;

    container.appendChild(toast);

    // Auto remove after duration
    setTimeout(() => {
        if (toast.parentElement) {
            toast.remove();
        }
    }, duration);
}

// Modal stacking system
let modalStack = [];
let modalZIndexBase = 10000;

// Modal management
function showModal(title, body, footerButtons = null) {
    const modal = document.getElementById('modalOverlay');
    const modalTitle = document.getElementById('modalTitle');
    const modalBody = document.getElementById('modalBody');
    const modalFooter = document.getElementById('modalFooter');

    modalTitle.textContent = title;
    modalBody.innerHTML = body;

    if (footerButtons) {
        modalFooter.innerHTML = footerButtons;
    } else {
        modalFooter.innerHTML = '<button class="btn btn-secondary" onclick="closeModal()">Close</button>';
    }

    // Add to stack and set z-index
    modalStack.push('modalOverlay');
    modal.style.zIndex = modalZIndexBase + modalStack.length;
    modal.style.display = 'flex';
}

function closeModal() {
    const modal = document.getElementById('modalOverlay');
    modal.style.display = 'none';
    // Remove from stack
    const index = modalStack.indexOf('modalOverlay');
    if (index > -1) {
        modalStack.splice(index, 1);
    }
}

// Generic modal show/hide with stacking support
function showModalWithId(modalId) {
    const modal = document.getElementById(modalId);
    if (!modal) return;

    // Add to stack and set z-index
    modalStack.push(modalId);
    modal.style.zIndex = modalZIndexBase + modalStack.length;
    modal.style.display = 'flex';
}

function closeModalWithId(modalId) {
    const modal = document.getElementById(modalId);
    if (!modal) return;

    modal.style.display = 'none';
    // Remove from stack
    const index = modalStack.indexOf(modalId);
    if (index > -1) {
        modalStack.splice(index, 1);
    }
}

// Add User Modal
function showAddUserModal() {
    showModalWithId('addUserModal');
    document.getElementById('username').focus();
}

function closeAddUserModal() {
    closeModalWithId('addUserModal');
    document.getElementById('addUserForm').reset();
}

function addUser(event) {
    event.preventDefault();

    const username = document.getElementById('username').value.trim().replace('@', '');
    const platform = (document.getElementById('platformSelect')?.value || 'tiktok');
    if (!username) {
        showToast('Please enter a username', 'error');
        return;
    }

    const submitBtn = event.target.querySelector('button[type="submit"]') ||
        document.querySelector('#addUserModal .btn-primary');
    const originalText = submitBtn.innerHTML;

    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="btn-icon">⏳</span>Adding...';

    fetch('/api/add_user', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ username: username, platform: platform })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showToast('User added successfully!', 'success');
                closeAddUserModal();
                setTimeout(() => location.reload(), 1000);
            } else {
                showToast(data.error || 'Failed to add user', 'error');
            }
        })
        .catch(error => {
            console.error('Error adding user:', error);
            showToast('Error adding user', 'error');
        })
        .finally(() => {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalText;
        });
}

// Tag Manager Modal
function showTagManagerModal() {
    showModalWithId('tagManagerModal');
    loadTags();
}

function closeTagManagerModal() {
    closeModalWithId('tagManagerModal');
    document.getElementById('createTagForm').reset();
}

function loadTags() {
    const tagListContent = document.getElementById('tagListContent');
    tagListContent.innerHTML = '<div class="loading-state">Loading tags...</div>';

    fetch('/api/tags')
        .then(response => response.json())
        .then(data => {
            if (!data.success) {
                tagListContent.innerHTML = `<div class="error-state">Error: ${data.error}</div>`;
                return;
            }

            const tags = data.tags;
            if (!tags || tags.length === 0) {
                tagListContent.innerHTML = '<div class="empty-state">No tags created yet.</div>';
                return;
            }

            let html = '<div class="tags-grid">';
            tags.forEach(tag => {
                html += `
                    <div class="tag-item" data-tag-id="${tag.id}">
                        <span class="tag" style="background-color: ${tag.color}">${escapeHtml(tag.name)}</span>
                        <div class="tag-actions">
                            <button class="btn btn-sm btn-secondary" onclick="editTag(${tag.id}, '${escapeHtml(tag.name)}', '${tag.color}')" title="Edit">
                                ✏️
                            </button>
                            <button class="btn btn-sm btn-danger" onclick="deleteTag(${tag.id}, '${escapeHtml(tag.name)}')" title="Delete">
                                🗑️
                            </button>
                        </div>
                    </div>
                `;
            });
            html += '</div>';
            tagListContent.innerHTML = html;
        })
        .catch(error => {
            console.error('Error loading tags:', error);
            tagListContent.innerHTML = '<div class="error-state">Failed to load tags. Please try again.</div>';
            showToast('Error loading tags', 'error');
        });
}

function createTag(event) {
    event.preventDefault();

    const tagName = document.getElementById('tagName').value.trim();
    const tagColor = document.getElementById('tagColor').value;

    if (!tagName) {
        showToast('Please enter a tag name', 'error');
        document.getElementById('tagName').focus();
        return;
    }

    if (tagName.length > 50) {
        showToast('Tag name is too long (max 50 characters)', 'error');
        return;
    }

    const submitBtn = event.target.querySelector('button[type="submit"]');
    const originalText = submitBtn.innerHTML;
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="btn-icon">⏳</span>Creating...';

    fetch('/api/tags', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            name: tagName,
            color: tagColor
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showToast(`Tag '${tagName}' created successfully!`, 'success');
                document.getElementById('createTagForm').reset();
                loadTags();
            } else {
                showToast(data.error || 'Failed to create tag', 'error');
            }
        })
        .catch(error => {
            console.error('Error creating tag:', error);
            showToast('Error creating tag', 'error');
        })
        .finally(() => {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalText;
        });
}

function editTag(tagId, currentName, currentColor) {
    const newName = prompt('Enter new tag name:', currentName);
    if (newName === null) return; // User cancelled

    const trimmedName = newName.trim();
    if (!trimmedName) {
        showToast('Tag name cannot be empty', 'error');
        return;
    }

    if (trimmedName.length > 50) {
        showToast('Tag name is too long (max 50 characters)', 'error');
        return;
    }

    const newColor = prompt('Enter new tag color (hex code):', currentColor);
    if (newColor === null) return; // User cancelled

    fetch(`/api/tags/${tagId}`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            name: trimmedName,
            color: newColor
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showToast(`Tag updated successfully!`, 'success');
                loadTags();
            } else {
                showToast(data.error || 'Failed to update tag', 'error');
            }
        })
        .catch(error => {
            console.error('Error updating tag:', error);
            showToast('Error updating tag', 'error');
        });
}

function deleteTag(tagId, tagName) {
    if (!confirm(`Are you sure you want to delete the tag '${tagName}'?\n\nThis will remove it from all users.`)) {
        return;
    }

    fetch(`/api/tags/${tagId}`, {
        method: 'DELETE'
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showToast(`Tag '${tagName}' deleted successfully!`, 'success');
                loadTags();
            } else {
                showToast(data.error || 'Failed to delete tag', 'error');
            }
        })
        .catch(error => {
            console.error('Error deleting tag:', error);
            showToast('Error deleting tag', 'error');
        });
}

// Utility function to escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Progress Modal
function showProgressModal(username) {
    currentProgressUsername = username;
    document.getElementById('progressTitle').textContent = `Download Progress - @${username}`;
    showModalWithId('progressModal');

    // Reset progress display
    document.getElementById('progressFill').style.width = '0%';
    document.getElementById('progressText').textContent = 'Starting download...';
    document.getElementById('filesDownloaded').textContent = '0';
    document.getElementById('currentFile').textContent = '-';
    document.getElementById('downloadStatus').textContent = 'Starting';
    document.getElementById('logContainer').textContent = '';

    // Start polling for progress
    startProgressPolling(username);
}

function closeProgressModal() {
    closeModalWithId('progressModal');
    if (progressPollingInterval) {
        clearInterval(progressPollingInterval);
        progressPollingInterval = null;
    }
    currentProgressUsername = null;
}

function startProgressPolling(username) {
    if (progressPollingInterval) {
        clearInterval(progressPollingInterval);
    }

    progressPollingInterval = setInterval(() => {
        fetch(`/api/download_progress/${username}`)
            .then(response => response.json())
            .then(data => {
                updateProgressDisplay(data);

                // Stop polling when download is complete or failed
                if (data.status === 'completed' || data.status === 'failed') {
                    clearInterval(progressPollingInterval);
                    progressPollingInterval = null;
                }
            })
            .catch(error => {
                console.error('Error polling progress:', error);
            });
    }, 1000);
}

function updateProgressDisplay(data) {
    const progressFill = document.getElementById('progressFill');
    const progressText = document.getElementById('progressText');
    const filesDownloaded = document.getElementById('filesDownloaded');
    const currentFile = document.getElementById('currentFile');
    const downloadStatus = document.getElementById('downloadStatus');
    const logContainer = document.getElementById('logContainer');

    // Update progress bar
    let percentage = 0;
    if (data.total_files && data.files_downloaded) {
        percentage = Math.round((data.files_downloaded / data.total_files) * 100);
    } else if (data.status === 'downloading' && data.files_downloaded) {
        percentage = Math.min(data.files_downloaded * 10, 90); // Estimate
    }

    progressFill.style.width = `${percentage}%`;

    // Update text
    if (data.status === 'completed') {
        progressText.textContent = `Download completed! Downloaded ${data.total_files || data.files_downloaded || 0} files.`;
    } else if (data.status === 'failed') {
        progressText.textContent = 'Download failed. Check logs for details.';
    } else if (data.status === 'downloading') {
        progressText.textContent = `Downloading... ${data.files_downloaded || 0} files downloaded`;
    } else {
        progressText.textContent = 'Preparing download...';
    }

    // Update details
    filesDownloaded.textContent = data.files_downloaded || 0;
    currentFile.textContent = data.current_file || '-';
    downloadStatus.textContent = data.status || 'Unknown';

    // Update logs
    if (data.logs && Array.isArray(data.logs)) {
        logContainer.textContent = data.logs.slice(-50).join('\n');
        logContainer.scrollTop = logContainer.scrollHeight;
    }
}

// Sync functions
function syncAllUsers() {
    const syncBtn = document.getElementById('syncAllBtn');
    if (syncBtn.disabled) return;

    fetch('/api/sync_all', {
        method: 'POST'
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showToast('Sync started...', 'info');
            } else {
                showToast(data.error || 'Failed to start sync', 'error');
            }
        })
        .catch(error => {
            console.error('Error starting sync:', error);
            showToast('Error starting sync', 'error');
        });
}

// Avatar refresh functions
function refreshAllAvatars() {
    const refreshBtn = document.getElementById('refreshAvatarsBtn');
    if (refreshBtn.disabled) return;

    const originalText = refreshBtn.innerHTML;
    refreshBtn.disabled = true;
    refreshBtn.innerHTML = '<span class="btn-icon">⏳</span>Refreshing...';

    fetch('/api/refresh_all_avatars', {
        method: 'POST'
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showToast('Avatar refresh started for all users...', 'info', 8000);
                showToast('This may take a while depending on the number of users', 'info', 6000);
            } else {
                showToast(data.error || 'Failed to start avatar refresh', 'error');
            }
        })
        .catch(error => {
            console.error('Error starting avatar refresh:', error);
            showToast('Error starting avatar refresh', 'error');
        })
        .finally(() => {
            refreshBtn.disabled = false;
            refreshBtn.innerHTML = originalText;
        });
}

function refreshUserAvatar(username) {
    fetch(`/api/refresh_avatar/${username}`, {
        method: 'POST'
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showToast(`Avatar refreshed for @${username}`, 'success');
                // Reload the page after a short delay to show the new avatar
                setTimeout(() => {
                    location.reload();
                }, 1000);
            } else {
                showToast(data.error || `Failed to refresh avatar for @${username}`, 'error');
            }
        })
        .catch(error => {
            console.error('Error refreshing avatar:', error);
            showToast(`Error refreshing avatar for @${username}`, 'error');
        });
}

function testAccess() {
    const testBtn = document.getElementById('testAccessBtn');
    const originalText = testBtn.innerHTML;

    testBtn.disabled = true;
    testBtn.innerHTML = '<span class="btn-icon">⏳</span>Testing...';

    fetch('/api/test_access')
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showToast('Access test successful!', 'success');
            } else {
                showToast(`Access test failed: ${data.message}`, 'error');
            }
        })
        .catch(error => {
            console.error('Error testing access:', error);
            showToast('Error testing access', 'error');
        })
        .finally(() => {
            testBtn.disabled = false;
            testBtn.innerHTML = originalText;
        });
}

// Keyboard shortcuts
document.addEventListener('keydown', function (event) {
    // Close modals with Escape key
    if (event.key === 'Escape') {
        // Close any open modals
        const modals = document.querySelectorAll('.modal-overlay[style*="flex"]');
        modals.forEach(modal => {
            modal.style.display = 'none';
        });

        // Close context menu
        const contextMenu = document.getElementById('userContextMenu');
        if (contextMenu && contextMenu.style.display === 'block') {
            contextMenu.style.display = 'none';
        }

        // Close lightbox
        const lightbox = document.getElementById('lightboxOverlay');
        if (lightbox && lightbox.style.display === 'flex') {
            lightbox.style.display = 'none';
            document.body.style.overflow = 'auto';
        }

        // Reset any forms in closed modals
        const externalModal = document.getElementById('externalDownloadModal');
        if (externalModal && externalModal.style.display === 'none') {
            const form = document.getElementById('externalDownloadForm');
            if (form) form.reset();
        }
    }

    // Quick add user with Ctrl+N or Cmd+N
    if ((event.ctrlKey || event.metaKey) && event.key === 'n') {
        event.preventDefault();
        showAddUserModal();
    }

    // Sync all users with Ctrl+R or Cmd+R (override browser refresh)
    if ((event.ctrlKey || event.metaKey) && event.key === 'r' && event.shiftKey) {
        event.preventDefault();
        syncAllUsers();
    }
});

// Click outside modal to close
document.addEventListener('click', function (event) {
    const modals = document.querySelectorAll('.modal-overlay');
    modals.forEach(modal => {
        if (event.target === modal) {
            closeModalWithId(modal.id);
        }
    });
});

// Auto-hide toast messages on click
document.addEventListener('click', function (event) {
    if (event.target.closest('.toast') && !event.target.closest('.toast-close')) {
        event.target.closest('.toast').remove();
    }
});

// Form submission helpers
function submitFormOnEnter(event, formId) {
    if (event.key === 'Enter') {
        event.preventDefault();
        document.getElementById(formId).dispatchEvent(new Event('submit'));
    }
}

// Utility functions
function formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
}

function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Search functionality
function initializeSearch() {
    const searchInput = document.getElementById('searchInput');
    if (!searchInput) return;

    const debouncedSearch = debounce(function (query) {
        filterUsers(query.toLowerCase());
    }, 300);

    searchInput.addEventListener('input', function (event) {
        debouncedSearch(event.target.value);
    });
}

function filterUsers(query) {
    const userCards = document.querySelectorAll('.user-card');
    let visibleCount = 0;

    userCards.forEach(card => {
        const username = card.dataset.username;
        const displayName = card.querySelector('.user-name a').textContent;
        const tags = Array.from(card.querySelectorAll('.tag')).map(t => t.textContent);

        const isVisible = username.toLowerCase().includes(query) ||
            displayName.toLowerCase().includes(query) ||
            tags.some(tag => tag.toLowerCase().includes(query));

        card.style.display = isVisible ? 'block' : 'none';
        if (isVisible) visibleCount++;
    });

    // Show no results message if needed
    const noResults = document.getElementById('noResultsMessage');
    if (noResults) {
        noResults.style.display = visibleCount === 0 ? 'block' : 'none';
    }
}

// Global download management
let downloadsPollingInterval = null;

function showDownloadsModal() {
    showModalWithId('downloadsModal');
    loadDownloadsList();
    startDownloadsPolling();
}

function closeDownloadsModal() {
    closeModalWithId('downloadsModal');
    if (downloadsPollingInterval) {
        clearInterval(downloadsPollingInterval);
        downloadsPollingInterval = null;
    }
}

function loadDownloadsList() {
    fetch('/api/downloads/status')
        .then(response => response.json())
        .then(data => {
            updateDownloadsStats(data);
            renderDownloadsList(data.downloads);
        })
        .catch(error => {
            console.error('Error loading downloads:', error);
            document.getElementById('downloadsList').innerHTML =
                '<div class="loading-state">Error loading downloads</div>';
        });
}

function updateDownloadsStats(data) {
    document.getElementById('totalDownloads').textContent = data.total_downloads;
    document.getElementById('activeDownloads').textContent = data.active_downloads;
    document.getElementById('completedDownloads').textContent = data.completed_downloads;
    document.getElementById('failedDownloads').textContent = data.failed_downloads;
}

function renderDownloadsList(downloads) {
    const container = document.getElementById('downloadsList');

    if (!downloads || downloads.length === 0) {
        container.innerHTML = `
            <div class="empty-downloads">
                <div class="empty-downloads-icon">📥</div>
                <p>No downloads yet</p>
            </div>
        `;
        return;
    }

    let html = '';
    downloads.forEach(download => {
        const startTime = new Date(download.start_time * 1000);
        const statusClass = download.status === 'downloading' ? 'active' : download.status;
        const progress = download.progress || 0;
        const canPause = download.status === 'downloading';
        const canResume = download.status === 'paused';

        html += `
            <div class="download-item ${statusClass}">
                <div class="download-info">
                    <div class="download-avatar">
                        ${download.username.charAt(0).toUpperCase()}
                    </div>
                    <div class="download-details">
                        <div class="download-username">@${download.username}</div>
                        <div class="download-status">
                            <span class="download-status-text">${download.status}</span>
                            ${download.current_file ? `<span> • ${download.current_file.substring(0, 30)}...</span>` : ''}
                            <span class="download-time"> • ${startTime.toLocaleTimeString()}</span>
                        </div>
                    </div>
                </div>
                <div class="download-progress ${download.status}">
                    <div class="download-progress-bar">
                        <div class="download-progress-fill" style="width: ${progress}%"></div>
                    </div>
                    <div class="download-progress-text">
                        ${download.files_downloaded || 0}${download.total_files ? `/${download.total_files}` : ''} files (${progress}%)
                    </div>
                    <div class="download-actions-row">
                        ${canPause ? `<button class="btn btn-sm btn-secondary download-action-btn" onclick="pauseDownload('${download.username}')" title="Pause Download">⏸️</button>` : ''}
                        ${canResume ? `<button class="btn btn-sm btn-primary download-action-btn" onclick="resumeDownload('${download.username}')" title="Resume Download">▶️</button>` : ''}
                    </div>
                </div>
            </div>
        `;
    });

    container.innerHTML = html;
}

function clearCompletedDownloads() {
    fetch('/api/downloads/clear_completed', {
        method: 'POST'
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showToast('Completed downloads cleared', 'success');
                loadDownloadsList();
            } else {
                showToast('Failed to clear downloads', 'error');
            }
        })
        .catch(error => {
            console.error('Error clearing downloads:', error);
            showToast('Error clearing downloads', 'error');
        });
}

function pauseDownload(username) {
    fetch(`/api/downloads/pause/${username}`, { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (d.success) {
                showToast(`Paused @${username}`, 'info');
                loadDownloadsList();
            } else {
                showToast(d.error || 'Failed to pause', 'error');
            }
        })
        .catch(() => showToast('Failed to pause', 'error'));
}

function resumeDownload(username) {
    fetch(`/api/downloads/resume/${username}`, { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (d.success) {
                showToast(`Resuming @${username}`, 'info');
                loadDownloadsList();
            } else {
                showToast(d.error || 'Failed to resume', 'error');
            }
        })
        .catch(() => showToast('Failed to resume', 'error'));
}

function startDownloadsPolling() {
    if (downloadsPollingInterval) {
        clearInterval(downloadsPollingInterval);
    }

    downloadsPollingInterval = setInterval(() => {
        loadDownloadsList();
    }, 2000);
}

// Settings UI
function loadSettings() {
    fetch('/api/settings')
        .then(r => r.json())
        .then(s => {
            const skip1 = document.getElementById('skipExistingToggle');
            const skip2 = document.getElementById('skipExistingToggleMenu');
            if (skip1) skip1.checked = !!s.skip_existing;
            if (skip2) skip2.checked = !!s.skip_existing;
            // Also refresh cookies list when settings open
            refreshCookiesList();

            // Pre-fill schedule modal fields
            const en = document.getElementById('scheduleEnabled');
            const freq = document.getElementById('scheduleFrequency');
            const time = document.getElementById('scheduleTime');
            const day = document.getElementById('scheduleDay');
            const dayContainer = document.getElementById('scheduleDayContainer');
            const timeout = document.getElementById('downloadTimeout');
            if (en) en.checked = !!s.schedule_enabled;
            if (freq) freq.value = s.schedule_frequency || 'daily';
            if (time) time.value = (s.schedule_time || '03:00').slice(0, 5);
            if (day) day.value = String(s.schedule_day ?? 0);
            if (dayContainer) dayContainer.style.display = (freq && freq.value === 'weekly') ? 'block' : 'none';
            if (timeout) timeout.value = s.download_timeout || 600;

            // Granular sync settings
            const sPosts = document.getElementById('syncPosts');
            const sStories = document.getElementById('syncStories');
            const sHighlights = document.getElementById('syncHighlights');
            if (sPosts) sPosts.checked = s.sync_posts !== undefined ? !!s.sync_posts : true;
            if (sStories) sStories.checked = s.sync_stories !== undefined ? !!s.sync_stories : true;
            if (sHighlights) sHighlights.checked = s.sync_highlights !== undefined ? !!s.sync_highlights : true;

            const lastRun = document.getElementById('lastRunInfo');
            if (lastRun) {
                lastRun.textContent = s.schedule_last_run ? `Last run: ${new Date(s.schedule_last_run).toLocaleString()}` : 'Last run: never';
            }
        })
        .catch(() => { });
}

function toggleSkipExisting(checkbox) {
    const value = !!checkbox.checked;
    fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skip_existing: value })
    }).then(r => r.json()).then(() => {
        // Mirror state across both toggles if both exist
        const other = (checkbox.id === 'skipExistingToggle') ? document.getElementById('skipExistingToggleMenu') : document.getElementById('skipExistingToggle');
        if (other) other.checked = value;
        showToast(`Skip existing ${value ? 'enabled' : 'disabled'}`, 'info');
    }).catch(() => {
        showToast('Failed to update setting', 'error');
        checkbox.checked = !value;
    });
}

function showScheduleModal() {
    loadSettings();
    showModalWithId('scheduleModal');
}
function closeScheduleModal() {
    closeModalWithId('scheduleModal');
}

// React to frequency change to show day picker
(function attachScheduleHandlers() {
    document.addEventListener('change', function (e) {
        if (e.target && e.target.id === 'scheduleFrequency') {
            const dayContainer = document.getElementById('scheduleDayContainer');
            dayContainer.style.display = (e.target.value === 'weekly') ? 'block' : 'none';
        }
    });
})();

// Settings sidebar and cookies modal
function openSettingsSidebar() { showModalWithId('settingsSidebar'); }
function closeSettingsSidebar() { closeModalWithId('settingsSidebar'); }

function openCookiesModal() { showModalWithId('cookiesModal'); refreshCookiesList(); }
function closeCookiesModal() { closeModalWithId('cookiesModal'); }

function refreshCookiesList() {
    fetch('/api/ig_cookies')
        .then(r => r.json())
        .then(d => {
            const list = document.getElementById('cookiesList');
            if (!list) return;
            const active = d.active || '';
            if (!d.files || d.files.length === 0) { list.innerHTML = '<p>No cookies uploaded.</p>'; return; }
            let html = '<ul>';
            d.files.forEach(f => {
                const isActive = f.name === active;
                html += `<li>${f.name} ${isActive ? '(active)' : ''}
              <button class="btn btn-sm btn-primary" onclick="activateCookies('${f.name}')">Use</button>
              <button class="btn btn-sm btn-secondary" onclick="deleteCookies('${f.name}')">Delete</button>
          </li>`;
            });
            html += '</ul>';
            list.innerHTML = html;
        }).catch(() => { });
}

function uploadCookiesFile() {
    const fileInput = document.getElementById('cookiesFile');
    if (!fileInput || !fileInput.files || !fileInput.files[0]) { showToast('Select a cookies.txt file', 'error'); return; }
    const fd = new FormData();
    fd.append('file', fileInput.files[0]);
    fetch('/api/ig_cookies/upload', { method: 'POST', body: fd })
        .then(r => r.json()).then(d => { if (d.success) { showToast('Cookies uploaded', 'success'); refreshCookiesList(); } else { showToast(d.error || 'Upload failed', 'error'); } })
        .catch(() => showToast('Upload failed', 'error'));
}

function activateCookies(name) {
    fetch('/api/ig_cookies/activate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) })
        .then(r => r.json()).then(() => { showToast('Active cookies set', 'success'); refreshCookiesList(); })
        .catch(() => showToast('Failed to set active cookies', 'error'));
}

function deleteCookies(name) {
    fetch(`/api/ig_cookies/${encodeURIComponent(name)}`, { method: 'DELETE' })
        .then(r => r.json()).then(d => { if (d.success) { showToast('Deleted', 'success'); refreshCookiesList(); } else { showToast(d.error || 'Delete failed', 'error'); } })
        .catch(() => showToast('Delete failed', 'error'));
}

// Sync All options menu
function toggleSyncOptions(event) {
    event.preventDefault();
    event.stopPropagation();
    const menu = document.getElementById('syncOptionsMenu');
    const rect = event.currentTarget.getBoundingClientRect();
    menu.style.left = `${Math.round(rect.left)}px`;
    menu.style.top = `${Math.round(rect.bottom + window.scrollY)}px`;
    menu.style.display = 'block';
    setTimeout(() => {
        document.addEventListener('click', hideSyncOptions, { once: true });
    }, 10);
}
function hideSyncOptions() {
    const menu = document.getElementById('syncOptionsMenu');
    if (menu) menu.style.display = 'none';
}

function saveSchedule() {
    const en = document.getElementById('scheduleEnabled').checked;
    const freq = document.getElementById('scheduleFrequency').value;
    const time = document.getElementById('scheduleTime').value || '03:00';
    const day = document.getElementById('scheduleDay').value || '0';
    const timeout = parseInt(document.getElementById('downloadTimeout').value || '0', 10);

    // Granular settings
    const syncPosts = document.getElementById('syncPosts').checked;
    const syncStories = document.getElementById('syncStories').checked;
    const syncHighlights = document.getElementById('syncHighlights').checked;

    const payload = {
        schedule_enabled: !!en,
        schedule_frequency: freq,
        schedule_time: time,
        schedule_day: parseInt(day, 10),
        sync_posts: !!syncPosts,
        sync_stories: !!syncStories,
        sync_highlights: !!syncHighlights
    };
    if (!isNaN(timeout) && timeout > 0) {
        payload.download_timeout = timeout;
    }

    fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    }).then(r => r.json()).then(() => {
        showToast('Settings saved', 'success');
        closeScheduleModal();
    }).catch(() => {
        showToast('Failed to save settings', 'error');
    });
}

function runSyncNow() {
    fetch('/api/sync_all', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (d.success) {
                showToast('Sync started', 'info');
                closeScheduleModal();
            } else {
                showToast(d.error || 'Failed to start sync', 'error');
            }
        })
        .catch(() => showToast('Failed to start sync', 'error'));
}

// Update global download indicator and sync status
function updateGlobalDownloadIndicator() {
    Promise.all([
        fetch('/api/downloads/status').then(r => r.json()),
        fetch('/api/sync_status').then(r => r.json())
    ])
        .then(([downloadData, syncData]) => {
            const indicator = document.getElementById('downloadIndicator');
            const badge = document.getElementById('downloadBadge');
            const progressFill = document.getElementById('globalProgressFill');
            const downloadCount = document.getElementById('downloadCount');
            const statusText = document.getElementById('statusText');
            const statusIndicator = document.getElementById('statusIndicator');
            const syncBtn = document.getElementById('syncAllBtn');

            // --- Handle Downloads UI (Always update badge/indicator hidden state) ---
            if (indicator && badge && progressFill && downloadCount) {
                if (downloadData.active_downloads > 0) {
                    indicator.style.display = 'flex';
                    badge.style.display = 'inline';
                    badge.textContent = downloadData.active_downloads;

                    // Calculate overall progress
                    const activeDownloads = downloadData.downloads.filter(d => d.status === 'downloading' || d.status === 'running');
                    if (activeDownloads.length > 0) {
                        const totalProgress = activeDownloads.reduce((sum, d) => sum + (d.progress || 0), 0);
                        const averageProgress = totalProgress / activeDownloads.length;
                        progressFill.style.width = `${averageProgress}%`;
                        downloadCount.textContent = downloadData.active_downloads;
                    }
                } else {
                    indicator.style.display = 'none';
                    if (downloadData.total_downloads === 0) {
                        badge.style.display = 'none';
                    } else {
                        badge.style.display = 'inline';
                        badge.textContent = downloadData.completed_downloads;
                        badge.style.background = 'var(--accent-success)';
                    }
                }
            }

            // --- Handle Main Status Text & Sync Button ---
            if (statusText && statusIndicator && syncBtn) {
                // Priority 1: Syncing
                if (syncData.running) {
                    if (syncData.current_timeout) {
                        statusIndicator.className = 'status-indicator timeout';
                        statusText.textContent = `⏱️ Timeout: ${syncData.current_user || 'unknown'}`;
                        syncBtn.innerHTML = '<span class="btn-icon">⏱️</span>Syncing (Timeout)...';
                    } else {
                        statusIndicator.className = 'status-indicator running';
                        statusText.textContent = `Syncing ${syncData.current_user || '...'}`;
                        syncBtn.innerHTML = '<span class="btn-icon">⏳</span>Syncing...';
                    }
                    syncBtn.disabled = true;

                    // Show immediate timeout notification
                    if (syncData.current_timeout && syncData.current_user) {
                        const timeoutMsg = `⏱️ ${syncData.current_user} is taking longer than expected`;
                        if (!document.querySelector('.toast')?.textContent.includes(syncData.current_user)) {
                            showToast(timeoutMsg, 'warning', 8000);
                        }
                    }
                    return; // Exit, Sync takes priority for text
                }

                // Sync not running, enable button
                syncBtn.disabled = false;
                syncBtn.innerHTML = '<span class="btn-icon">🔄</span>Sync All';

                // Priority 2: Active Downloads (including Avatar Refresh)
                if (downloadData.active_downloads > 0) {
                    statusIndicator.className = 'status-indicator active';
                    statusIndicator.style.background = 'var(--accent-primary)';

                    const activeDownloads = downloadData.downloads.filter(d => d.status === 'downloading' || d.status === 'running');
                    const refreshTask = activeDownloads.find(d => d.username === 'Refresh Avatars');

                    if (refreshTask) {
                        statusText.textContent = `Refreshing Avatars (${refreshTask.progress}%)`;
                    } else {
                        statusText.textContent = `Downloading (${downloadData.active_downloads})...`;
                    }
                    return;
                }

                // Priority 3: Ready / Idle
                statusIndicator.className = 'status-indicator ready';
                statusIndicator.style.background = 'var(--accent-success)'; // Ensure green

                if (syncData.timeout_users && syncData.timeout_users.length > 0) {
                    statusText.textContent = `Ready (${syncData.timeout_users.length} timeouts)`;
                    statusText.title = `Timed out users: ${syncData.timeout_users.join(', ')}`;
                } else {
                    statusText.textContent = 'Ready';
                    statusText.title = '';
                }
            }
        })
        .catch(error => {
            console.error('Error updating global indicator:', error);
        });
}

// Tag Assignment Functions
let currentTagUser = null;
let userTags = [];
let allAvailableTags = [];

function showUserTagModal(username) {
    currentTagUser = username;
    const modal = document.getElementById('userTagModal');
    const title = document.getElementById('userTagModalTitle');
    const content = document.getElementById('userTagModalContent');

    title.textContent = `Assign Tags to @${username}`;
    content.innerHTML = '<div class="loading-state">Loading tags...</div>';
    showModalWithId('userTagModal');

    // Reset state
    userTags = [];
    allAvailableTags = [];

    // Load all tags and user's current tags
    Promise.all([
        fetch('/api/tags').then(r => r.json()),
        fetch(`/api/users/${username}/tags`).then(r => r.json())
    ])
        .then(([allTagsResponse, userTagsResponse]) => {
            // Handle API responses
            if (allTagsResponse.success) {
                allAvailableTags = allTagsResponse.tags || [];
            } else {
                throw new Error(allTagsResponse.error || 'Failed to load all tags');
            }

            if (userTagsResponse.success) {
                userTags = (userTagsResponse.tags || []).map(t => t.id);
            } else {
                // User might not have tags yet, that's ok
                userTags = [];
            }

            renderTagAssignment();
        })
        .catch(error => {
            console.error('Error loading tags:', error);
            content.innerHTML = `
            <div class="error-state">
                <p>Error loading tags: ${error.message}</p>
                <button class="btn btn-secondary" onclick="showUserTagModal('${username}')">Retry</button>
                <p><a href="#" onclick="closeUserTagModal(); showTagManagerModal();">Or create some tags first</a></p>
            </div>
        `;
            showToast('Error loading tags', 'error');
        });
}

function renderTagAssignment() {
    const content = document.getElementById('userTagModalContent');

    if (!allAvailableTags || allAvailableTags.length === 0) {
        content.innerHTML = `
            <div class="empty-state">
                <p>No tags available.</p>
                <button class="btn btn-primary" onclick="closeUserTagModal(); showTagManagerModal();">Create Tags</button>
            </div>
        `;
        return;
    }

    let html = '<div class="tag-assignment-list">';
    allAvailableTags.forEach(tag => {
        const isChecked = userTags.includes(tag.id);
        html += `
            <div class="tag-assignment-item">
                <label class="tag-checkbox">
                    <input type="checkbox" 
                           value="${tag.id}" 
                           ${isChecked ? 'checked' : ''}
                           onchange="toggleUserTag(${tag.id}, this.checked)">
                    <span class="tag" style="background-color: ${tag.color}">${escapeHtml(tag.name)}</span>
                </label>
            </div>
        `;
    });
    html += '</div>';

    html += `
        <div class="tag-assignment-summary">
            <p><strong>Selected:</strong> <span id="selectedTagCount">${userTags.length}</span> tag(s)</p>
        </div>
    `;

    content.innerHTML = html;
    updateSelectedTagCount();
}

function toggleUserTag(tagId, isChecked) {
    if (isChecked) {
        if (!userTags.includes(tagId)) {
            userTags.push(tagId);
        }
    } else {
        userTags = userTags.filter(id => id !== tagId);
    }
    updateSelectedTagCount();
}

function updateSelectedTagCount() {
    const countElement = document.getElementById('selectedTagCount');
    if (countElement) {
        countElement.textContent = userTags.length;
    }
}

function saveUserTags() {
    if (!currentTagUser) {
        showToast('No user selected', 'error');
        return;
    }

    const saveBtn = document.querySelector('#userTagModal .btn-primary');
    if (!saveBtn) {
        showToast('Save button not found', 'error');
        return;
    }

    const originalText = saveBtn.textContent;
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving...';

    // Use the new PUT endpoint to replace all user tags at once
    fetch(`/api/users/${currentTagUser}/tags`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            tag_ids: userTags
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showToast(`Tags updated for @${currentTagUser}!`, 'success');
                closeUserTagModal();
                // Reload page to show updated tags
                setTimeout(() => location.reload(), 1000);
            } else {
                throw new Error(data.error || 'Failed to update tags');
            }
        })
        .catch(error => {
            console.error('Error saving tags:', error);
            showToast(`Error saving tags: ${error.message}`, 'error');
        })
        .finally(() => {
            saveBtn.disabled = false;
            saveBtn.textContent = originalText;
        });
}

function closeUserTagModal() {
    closeModalWithId('userTagModal');

    // Reset state
    currentTagUser = null;
    userTags = [];
    allAvailableTags = [];
}

// External Download Functions
function showExternalDownloadModal() {
    showModalWithId('externalDownloadModal');
    document.getElementById('externalUrl').focus();
}

function closeExternalDownloadModal() {
    closeModalWithId('externalDownloadModal');
    document.getElementById('externalDownloadForm').reset();
}

function startExternalDownload(event) {
    event.preventDefault();

    const url = document.getElementById('externalUrl').value.trim();
    const destination = document.getElementById('externalDestination').value.trim();

    if (!url) {
        showToast('Please enter a URL', 'error');
        return;
    }

    // Validate URL format
    try {
        new URL(url);
    } catch (e) {
        showToast('Please enter a valid URL', 'error');
        return;
    }

    // Check if URL is from supported services
    const supportedDomains = [
        'drive.google.com', 'docs.google.com',  // Google Drive
        'gofile.io',                             // GoFile
        'bunkr.', 'bunkrr.',                     // Bunkr
        'imgur.com',                             // Imgur
        'catbox.moe',                            // Catbox
        'redgifs.com'                            // RedGifs
    ];
    if (!supportedDomains.some(domain => url.includes(domain))) {
        showToast('Unsupported service. Please use Google Drive, GoFile, Bunkr, Imgur, Catbox, or RedGifs URLs.', 'warning');
        // Allow anyway in case it's supported
    }

    const submitBtn = event.target.querySelector('button[type="submit"]') ||
        document.querySelector('#externalDownloadModal .btn-primary');
    const originalText = submitBtn.innerHTML;

    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="btn-icon">⏳</span>Starting...';

    fetch('/api/external_download', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            url: url,
            destination: destination || null
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showToast('External download started! Check the Download Manager for progress.', 'success');
                closeExternalDownloadModal();
                closeSettingsSidebar(); // Close settings sidebar too

                // Show download manager after a brief delay
                setTimeout(() => {
                    showDownloadsModal();
                }, 1000);
            } else {
                showToast(data.error || 'Failed to start download', 'error');
            }
        })
        .catch(error => {
            console.error('Error starting external download:', error);
            showToast('Error starting download', 'error');
        })
        .finally(() => {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalText;
        });
}

// Initialize everything when DOM is ready
document.addEventListener('DOMContentLoaded', function () {
    // Initialize search if present
    initializeSearch();

    // Set up form enter key handlers
    const usernameInput = document.getElementById('username');
    if (usernameInput) {
        usernameInput.addEventListener('keydown', function (event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                document.getElementById('addUserForm').dispatchEvent(new Event('submit'));
            }
        });
    }

    const tagNameInput = document.getElementById('tagName');
    if (tagNameInput) {
        tagNameInput.addEventListener('keydown', function (event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                document.getElementById('createTagForm').dispatchEvent(new Event('submit'));
            }
        });
    }

    // Auto-focus inputs when modals open
    const addUserModal = document.getElementById('addUserModal');
    if (addUserModal) {
        const observer = new MutationObserver(function (mutations) {
            mutations.forEach(function (mutation) {
                if (mutation.type === 'attributes' && mutation.attributeName === 'style') {
                    if (addUserModal.style.display === 'flex') {
                        setTimeout(() => {
                            const input = addUserModal.querySelector('input[type="text"]');
                            if (input) input.focus();
                        }, 100);
                    }
                }
            });
        });
        observer.observe(addUserModal, { attributes: true });
    }

    // Show welcome message for new users
    if (window.location.search.includes('welcome=true')) {
        setTimeout(() => {
            showToast('Welcome to TrackUI! Start by adding some users to track.', 'info', 10000);
        }, 1000);
    }

    // Start auto-refresh for sync status and download indicator
    setInterval(() => {
        updateGlobalDownloadIndicator();
    }, 2000);
    updateGlobalDownloadIndicator(); // Initial call

    // Load settings and reflect in UI
    loadSettings();

    console.log('TikTok Tracker initialized successfully!');
});

// Instagram Following functionality
let followingData = [];
let selectedProfiles = new Set();

function showInstagramFollowingModal() {
    document.getElementById('instagramFollowingModal').style.display = 'flex';
    resetFollowingModal();
}

function closeInstagramFollowingModal() {
    closeModalWithId('instagramFollowingModal');

    // Clear following data
    followingData = [];
    selectedProfiles.clear();

    // Clear status
    document.getElementById('fetchStatus').innerHTML = '';
    document.getElementById('followingList').innerHTML = '';
    document.getElementById('selectedCount').textContent = '0 selected';
    document.getElementById('followingSearch').value = '';
}

// Handle cookie file selection
document.addEventListener('DOMContentLoaded', function () {
    const cookieFileInput = document.getElementById('instagramCookieFile');
    if (cookieFileInput) {
        cookieFileInput.addEventListener('change', function () {
            const file = this.files[0];
            if (file) {
                document.getElementById('cookieFileName').textContent = file.name;
                document.getElementById('uploadCookieBtn').style.display = 'inline-block';
                document.getElementById('debugCookieBtn').style.display = 'inline-block';
            } else {
                document.getElementById('cookieFileName').textContent = '';
                document.getElementById('uploadCookieBtn').style.display = 'none';
                document.getElementById('debugCookieBtn').style.display = 'none';
            }
        });
    }
});

function uploadInstagramCookie() {
    const fileInput = document.getElementById('instagramCookieFile');
    const file = fileInput.files[0];

    if (!file) {
        showToast('Please select a cookie file first', 'error');
        return;
    }

    const formData = new FormData();
    formData.append('file', file);

    const uploadBtn = document.getElementById('uploadCookieBtn');
    const originalText = uploadBtn.innerHTML;
    uploadBtn.disabled = true;
    uploadBtn.innerHTML = '<span class="btn-icon">⏳</span>Uploading...';

    fetch('/api/instagram_following/upload_cookie', {
        method: 'POST',
        body: formData
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showToast('Cookie uploaded successfully!', 'success');

                // Move to step 2
                document.getElementById('step1').style.display = 'none';
                document.getElementById('step2').style.display = 'block';
            } else {
                showToast(data.error || 'Failed to upload cookie', 'error');
            }
        })
        .catch(error => {
            console.error('Error uploading cookie:', error);
            showToast('Error uploading cookie', 'error');
        })
        .finally(() => {
            uploadBtn.disabled = false;
            uploadBtn.innerHTML = originalText;
        });
}

function debugInstagramCookie() {
    const fileInput = document.getElementById('instagramCookieFile');
    const file = fileInput.files[0];

    if (!file) {
        showToast('Please select a cookie file first', 'error');
        return;
    }

    const formData = new FormData();
    formData.append('file', file);

    const debugBtn = document.getElementById('debugCookieBtn');
    const originalText = debugBtn.innerHTML;
    debugBtn.disabled = true;
    debugBtn.innerHTML = '<span class="btn-icon">⏳</span>Analyzing...';

    fetch('/api/instagram_following/debug_cookie', {
        method: 'POST',
        body: formData
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const analysis = data.analysis;

                let debugInfo = `
                <div class="cookie-debug-info">
                    <h4>📋 Cookie File Analysis: ${data.filename}</h4>
                    <div class="debug-stats">
                        <div class="stat-row"><strong>File size:</strong> ${(data.file_size / 1024).toFixed(1)} KB</div>
                        <div class="stat-row"><strong>Total lines:</strong> ${analysis.total_lines}</div>
                        <div class="stat-row"><strong>Non-empty lines:</strong> ${analysis.non_empty_lines}</div>
                        <div class="stat-row"><strong>Comment lines:</strong> ${analysis.comment_lines}</div>
                        <div class="stat-row"><strong>Cookie lines:</strong> ${analysis.cookie_lines}</div>
                        <div class="stat-row"><strong>Instagram cookies:</strong> ${analysis.instagram_lines}</div>
                    </div>
                    
                    ${analysis.instagram_lines > 0 ? `
                        <div class="debug-success">
                            ✅ <strong>Good!</strong> Found ${analysis.instagram_lines} Instagram cookies
                        </div>
                        <div class="debug-domains">
                            <strong>Instagram domains found:</strong><br>
                            ${analysis.instagram_domains.map(d => `• ${d}`).join('<br>')}
                        </div>
                        <div class="debug-cookies">
                            <strong>Cookie names found:</strong><br>
                            ${analysis.cookie_names.slice(0, 10).map(c => `• ${c}`).join('<br>')}
                            ${analysis.cookie_names.length > 10 ? `<br>... and ${analysis.cookie_names.length - 10} more` : ''}
                        </div>
                    ` : `
                        <div class="debug-error">
                            ❌ <strong>Problem:</strong> No Instagram cookies found in this file
                        </div>
                        <div class="debug-help">
                            Make sure you:
                            <ul>
                                <li>Exported cookies from instagram.com (not other sites)</li>
                                <li>Were logged in to Instagram when exporting</li>
                                <li>Used a browser extension that exports in Netscape format</li>
                            </ul>
                        </div>
                    `}
                    
                    ${analysis.sample_lines.length > 0 ? `
                        <div class="debug-sample">
                            <strong>Sample lines:</strong><br>
                            ${analysis.sample_lines.map(line => `<code>${escapeHtml(line)}</code>`).join('<br>')}
                        </div>
                    ` : ''}
                </div>
            `;

                showModal('Cookie File Analysis', debugInfo, `
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
                ${analysis.instagram_lines > 0 ? '<button class="btn btn-primary" onclick="closeModal(); uploadInstagramCookie();">Upload This File</button>' : ''}
            `);

            } else {
                showToast(data.error || 'Failed to analyze cookie file', 'error');
            }
        })
        .catch(error => {
            console.error('Error analyzing cookie:', error);
            showToast('Error analyzing cookie file', 'error');
        })
        .finally(() => {
            debugBtn.disabled = false;
            debugBtn.innerHTML = originalText;
        });
}

function testInstagramAccess() {
    const testBtn = document.getElementById('testAccessBtn');
    const originalText = testBtn.innerHTML;
    testBtn.disabled = true;
    testBtn.innerHTML = '<span class="btn-icon">⏳</span>Testing...';

    fetch('/api/instagram_following/test_access', {
        method: 'POST'
    })
        .then(response => response.json())
        .then(data => {
            const analysis = data.analysis || {};
            const loginIndicators = analysis.logged_in_indicators || {};
            const authTokens = analysis.auth_tokens || {};

            let testResults = `
            <div class="access-test-results">
                <h4>🔍 Instagram Access Test Results</h4>
                
                <div class="test-section">
                    <h5>Connection Status</h5>
                    <div class="test-stat">
                        <strong>HTTP Status:</strong> ${analysis.status_code || 'Unknown'}
                        ${analysis.status_code === 200 ? ' ✅' : ' ❌'}
                    </div>
                    <div class="test-stat">
                        <strong>Response Size:</strong> ${analysis.response_size ? (analysis.response_size / 1024).toFixed(1) + ' KB' : 'Unknown'}
                    </div>
                    <div class="test-stat">
                        <strong>Cookies Loaded:</strong> ${analysis.cookies_loaded || 0}
                    </div>
                </div>
                
                <div class="test-section">
                    <h5>Authentication Status</h5>
                    <div class="test-stat">
                        <strong>Login Form Present:</strong> ${loginIndicators.has_login_form ? 'Yes ❌' : 'No ✅'}
                    </div>
                    <div class="test-stat">
                        <strong>Viewer Data Found:</strong> ${loginIndicators.has_viewer_data ? 'Yes ✅' : 'No ❌'}
                    </div>
                    <div class="test-stat">
                        <strong>Shared Data Found:</strong> ${loginIndicators.has_shared_data ? 'Yes ✅' : 'No ❌'}
                    </div>
                    <div class="test-stat">
                        <strong>CSRF Token Found:</strong> ${authTokens.csrf_token_found ? 'Yes ✅' : 'No ❌'}
                    </div>
                    <div class="test-stat">
                        <strong>User ID Found:</strong> ${authTokens.user_id_found ? 'Yes ✅' : 'No ❌'}
                    </div>
                    ${authTokens.user_id ? `<div class="test-stat"><strong>User ID:</strong> ${authTokens.user_id}</div>` : ''}
                </div>
                
                <div class="test-section">
                    <h5>Overall Assessment</h5>
                    <div class="test-result ${data.success ? 'success' : 'error'}">
                        ${data.success ? '✅ Access looks good! You should be able to fetch following list.' : '❌ Access issues detected. See problems below.'}
                    </div>
                    
                    ${!data.success ? `
                        <div class="test-recommendations">
                            <h6>Recommendations:</h6>
                            <ul>
                                ${!loginIndicators.has_viewer_data && loginIndicators.has_login_form ? '<li>Your cookies may be expired. Try getting fresh cookies.</li>' : ''}
                                ${analysis.status_code !== 200 ? '<li>Instagram may be blocking requests. Wait a few minutes and try again.</li>' : ''}
                                ${analysis.cookies_loaded === 0 ? '<li>No cookies were loaded. Check your cookie file format.</li>' : ''}
                                ${!authTokens.csrf_token_found ? '<li>Could not find CSRF token. Instagram may have changed their format.</li>' : ''}
                                ${!authTokens.user_id_found ? '<li>Could not find user ID. Make sure you\'re logged into Instagram when exporting cookies.</li>' : ''}
                            </ul>
                        </div>
                    ` : ''}
                </div>
                
                ${analysis.response_sample ? `
                    <details class="test-details">
                        <summary>Response Sample (for debugging)</summary>
                        <pre><code>${escapeHtml(analysis.response_sample)}</code></pre>
                    </details>
                ` : ''}
            </div>
        `;

            showModal('Instagram Access Test', testResults, `
            <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            ${data.success ? '<button class="btn btn-primary" onclick="closeModal(); fetchInstagramFollowing();">Fetch Following</button>' : ''}
        `);

        })
        .catch(error => {
            console.error('Error testing access:', error);
            showToast('Error testing Instagram access', 'error');
        })
        .finally(() => {
            testBtn.disabled = false;
            testBtn.innerHTML = originalText;
        });
}

function fetchInstagramFollowing() {
    const fetchBtn = document.getElementById('fetchFollowingBtn');
    const statusDiv = document.getElementById('fetchStatus');

    const originalText = fetchBtn.innerHTML;
    fetchBtn.disabled = true;
    fetchBtn.innerHTML = '<span class="btn-icon">⏳</span>Fetching...';

    statusDiv.innerHTML = '<div class="loading-indicator">🔄 Fetching following list from Instagram...</div>';

    fetch('/api/instagram_following/fetch', {
        method: 'POST'
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                followingData = data.following || [];

                if (followingData.length === 0) {
                    statusDiv.innerHTML = '<div class="error-indicator">⚠️ No following accounts found</div>';
                    showToast('No following accounts found', 'warning');
                    return;
                }

                statusDiv.innerHTML = `<div class="success-indicator">✅ Found ${followingData.length} following accounts</div>`;
                showToast(`Found ${followingData.length} accounts you're following`, 'success');

                // Move to step 3
                document.getElementById('step2').style.display = 'none';
                document.getElementById('step3').style.display = 'block';

                // Populate following list and automatically load profile pictures
                renderFollowingList();
                loadProfilePicturesAutomatically();
            } else {
                statusDiv.innerHTML = `<div class="error-indicator">❌ ${data.error || 'Failed to fetch following list'}</div>`;
                showToast(data.error || 'Failed to fetch following list', 'error');
            }
        })
        .catch(error => {
            console.error('Error fetching following:', error);
            statusDiv.innerHTML = '<div class="error-indicator">❌ Error occurred while fetching</div>';
            showToast('Error fetching following list', 'error');
        })
        .finally(() => {
            fetchBtn.disabled = false;
            fetchBtn.innerHTML = originalText;
        });
}

function renderFollowingList() {
    const listContainer = document.getElementById('followingList');
    const searchTerm = document.getElementById('followingSearch').value.toLowerCase();

    // Filter data based on search
    const filteredData = followingData.filter(profile =>
        profile.username.toLowerCase().includes(searchTerm) ||
        (profile.display_name && profile.display_name.toLowerCase().includes(searchTerm))
    );

    if (filteredData.length === 0) {
        listContainer.innerHTML = '<div class="empty-state">No profiles match your search</div>';
        return;
    }

    let html = '<div class="following-profiles-grid">';

    filteredData.forEach(profile => {
        const isSelected = selectedProfiles.has(profile.username);
        html += `
            <div class="following-profile-item ${isSelected ? 'selected' : ''}" data-username="${profile.username}">
                <div class="profile-checkbox">
                    <input type="checkbox" 
                           id="profile_${profile.username}" 
                           ${isSelected ? 'checked' : ''}
                           onchange="toggleProfileSelection('${profile.username}', this.checked)">
                </div>
                <div class="profile-avatar">
                    ${profile.profile_picture ?
                `<img src="${profile.profile_picture}" alt="${profile.username}" loading="lazy" onload="this.nextElementSibling.style.display='none'; this.style.display='block';" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">` :
                ''
            }
                    <div class="avatar-placeholder" style="display:${profile.profile_picture ? 'none' : 'flex'}; width:100%; height:100%; border-radius:50%; background:var(--accent-primary); color:white; font-weight:bold; justify-content:center; align-items:center;">${(profile.display_name || profile.username)[0].toUpperCase()}</div>
                </div>
                <div class="profile-info">
                    <div class="profile-display-name">${escapeHtml(profile.display_name || profile.username)}</div>
                    <div class="profile-username">@${escapeHtml(profile.username)}</div>
                    ${profile.follower_count ? `<div class="profile-followers">${formatNumber(profile.follower_count)} followers</div>` : ''}
                </div>
            </div>
        `;
    });

    html += '</div>';
    listContainer.innerHTML = html;

    updateSelectedCount();
}

function toggleProfileSelection(username, isSelected) {
    if (isSelected) {
        selectedProfiles.add(username);
    } else {
        selectedProfiles.delete(username);
    }

    // Update visual state
    const profileItem = document.querySelector(`[data-username="${username}"]`);
    if (profileItem) {
        profileItem.classList.toggle('selected', isSelected);
    }

    updateSelectedCount();
}

function updateSelectedCount() {
    document.getElementById('selectedCount').textContent = `${selectedProfiles.size} selected`;
}

function selectAllFollowing() {
    const checkboxes = document.querySelectorAll('.following-profile-item input[type="checkbox"]');
    checkboxes.forEach(checkbox => {
        if (!checkbox.checked) {
            checkbox.checked = true;
            checkbox.dispatchEvent(new Event('change'));
        }
    });
}

function deselectAllFollowing() {
    const checkboxes = document.querySelectorAll('.following-profile-item input[type="checkbox"]');
    checkboxes.forEach(checkbox => {
        if (checkbox.checked) {
            checkbox.checked = false;
            checkbox.dispatchEvent(new Event('change'));
        }
    });
}

function filterFollowingList() {
    renderFollowingList();
}

function loadProfilePictures() {
    if (followingData.length === 0) {
        showToast('No profiles loaded to fetch pictures for', 'warning');
        return;
    }

    const usernames = followingData.map(profile => profile.username);
    const loadBtn = document.querySelector('button[onclick="loadProfilePictures()"]');

    const originalText = loadBtn.innerHTML;
    loadBtn.disabled = true;
    loadBtn.innerHTML = '<span class="btn-icon">⏳</span>Loading...';

    fetch('/api/instagram_following/fetch_profile_pics', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            usernames: usernames
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const profilePics = data.profile_pictures || {};
                const fetchedCount = data.fetched_count || 0;

                // Update followingData with profile pictures
                followingData.forEach(profile => {
                    if (profilePics[profile.username]) {
                        profile.profile_picture = profilePics[profile.username];
                    }
                });

                // Re-render the following list with updated profile pictures
                renderFollowingList();

                showToast(`Loaded ${fetchedCount} profile pictures`, 'success');

            } else {
                showToast(data.error || 'Failed to load profile pictures', 'error');
            }
        })
        .catch(error => {
            console.error('Error loading profile pictures:', error);
            showToast('Error loading profile pictures', 'error');
        })
        .finally(() => {
            loadBtn.disabled = false;
            loadBtn.innerHTML = originalText;
        });
}

// Automatic profile picture loading function
function loadProfilePicturesAutomatically() {
    if (followingData.length === 0) {
        return;
    }

    const usernames = followingData.map(profile => profile.username);

    // Show a subtle loading indicator
    showToast('Loading profile pictures...', 'info', 2000);

    fetch('/api/instagram_following/fetch_profile_pics', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            usernames: usernames
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const profilePics = data.profile_pictures || {};
                const fetchedCount = data.fetched_count || 0;

                // Update followingData with profile pictures
                followingData.forEach(profile => {
                    if (profilePics[profile.username]) {
                        profile.profile_picture = profilePics[profile.username];
                    }
                });

                // Re-render the following list with updated profile pictures
                renderFollowingList();

                if (fetchedCount > 0) {
                    showToast(`Loaded ${fetchedCount} profile pictures`, 'success', 3000);
                }

            } else {
                console.warn('Failed to load profile pictures:', data.error);
                // Don't show error toast since this is automatic
            }
        })
        .catch(error => {
            console.error('Error loading profile pictures:', error);
            // Don't show error toast since this is automatic
        });
}

function addSelectedProfiles() {
    if (selectedProfiles.size === 0) {
        showToast('Please select at least one profile to add', 'warning');
        return;
    }

    const selectedUsernames = Array.from(selectedProfiles);
    const addBtn = document.getElementById('addSelectedBtn');

    const originalText = addBtn.innerHTML;
    addBtn.disabled = true;
    addBtn.innerHTML = '<span class="btn-icon">⏳</span>Adding...';

    fetch('/api/instagram_following/add_selected', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            usernames: selectedUsernames
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const added = data.added || 0;
                const skipped = data.skipped || 0;

                let message = `Successfully added ${added} profile${added !== 1 ? 's' : ''}`;
                if (skipped > 0) {
                    message += ` (${skipped} already existed)`;
                }

                showToast(message, 'success');

                // Close modal and reload page to show new users
                closeInstagramFollowingModal();
                setTimeout(() => {
                    location.reload();
                }, 1000);
            } else {
                showToast(data.error || 'Failed to add profiles', 'error');
            }
        })
        .catch(error => {
            console.error('Error adding profiles:', error);
            showToast('Error adding profiles', 'error');
        })
        .finally(() => {
            addBtn.disabled = false;
            addBtn.innerHTML = originalText;
        });
}

// Helper function to format numbers
function formatNumber(num) {
    if (num >= 1000000) {
        return (num / 1000000).toFixed(1) + 'M';
    } else if (num >= 1000) {
        return (num / 1000).toFixed(1) + 'K';
    }
    return num.toString();
}

// Scheduler Logs Modal
function showSchedulerLogsModal() {
    showModalWithId('schedulerLogsModal');
    loadSchedulerLogs();
}

function closeSchedulerLogsModal() {
    closeModalWithId('schedulerLogsModal');
}

function loadSchedulerLogs() {
    fetch('/api/scheduler/status')
        .then(response => response.json())
        .then(data => {
            // Update status info
            document.getElementById('schedulerStatus').textContent =
                data.enabled ? (data.running ? '✅ Running' : '⏸️ Enabled but not started') : '❌ Disabled';
            document.getElementById('schedulerFrequency').textContent =
                data.frequency || 'Not set';
            document.getElementById('schedulerTime').textContent =
                data.time || 'Not set';
            document.getElementById('schedulerLastRun').textContent =
                data.last_run || 'Never';
            document.getElementById('schedulerNextRun').textContent =
                data.next_run || 'Not scheduled';

            // Update logs list
            const logsList = document.getElementById('schedulerLogsList');
            if (data.recent_logs && data.recent_logs.length > 0) {
                let logsHtml = '<div class=\"log-entries\">';
                data.recent_logs.forEach(log => {
                    logsHtml += `<div class=\"log-entry\">${escapeHtml(log)}</div>`;
                });
                logsHtml += '</div>';
                logsList.innerHTML = logsHtml;
            } else {
                logsList.innerHTML = '<div class=\"empty-state\">No logs yet</div>';
            }
        })
        .catch(error => {
            console.error('Error loading scheduler logs:', error);
            document.getElementById('schedulerLogsList').innerHTML =
                '<div class=\"error-state\">Failed to load logs</div>';
        });
}

function refreshSchedulerLogs() {
    loadSchedulerLogs();
    showToast('Logs refreshed', 'success', 2000);
}

function openSettingsSidebar() {
    document.getElementById('settingsSidebar').style.display = 'flex';
}

function closeSettingsSidebar() {
    document.getElementById('settingsSidebar').style.display = 'none';
}

// View Toggle Functionality
function toggleView(viewType) {
    const usersGrid = document.getElementById('usersGrid');
    const gridBtn = document.getElementById('gridViewBtn');
    const listBtn = document.getElementById('listViewBtn');

    if (!usersGrid || !gridBtn || !listBtn) return;

    if (viewType === 'list') {
        usersGrid.classList.add('list-view');
        gridBtn.classList.remove('active');
        listBtn.classList.add('active');
        localStorage.setItem('viewPreference', 'list');
    } else {
        usersGrid.classList.remove('list-view');
        gridBtn.classList.add('active');
        listBtn.classList.remove('active');
        localStorage.setItem('viewPreference', 'grid');
    }
}

// Initialize view from local storage
document.addEventListener('DOMContentLoaded', function () {
    const savedView = localStorage.getItem('viewPreference') || 'grid';
    toggleView(savedView);
});

// Database Import/Export
function exportDatabase() {
    window.location.href = '/api/settings/export';
}

function triggerImportDatabase() {
    document.getElementById('importDatabaseFile').click();
}

function importDatabase(input) {
    if (!input.files || !input.files[0]) return;

    const file = input.files[0];
    const formData = new FormData();
    formData.append('file', file);

    showToast('Importing database...', 'info');

    fetch('/api/settings/import', {
        method: 'POST',
        body: formData
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showToast('Import successful! Reloading...', 'success');
                setTimeout(() => location.reload(), 2000);
            } else {
                showToast(data.error || 'Import failed', 'error');
            }
        })
        .catch(error => {
            console.error('Import error:', error);
            showToast('Import error: ' + error, 'error');
        })
        .finally(() => {
            input.value = ''; // Reset input
        });
}

// Factory Reset Logic
function showResetModal() {
    showModalWithId('resetConfirmationModal');
    document.getElementById('resetConfirmationInput').value = '';
    document.getElementById('confirmResetBtn').disabled = true;
    document.getElementById('resetConfirmationInput').focus();
}

function closeResetModal() {
    closeModalWithId('resetConfirmationModal');
    document.getElementById('resetConfirmationInput').value = '';
}

// Enable confirm button only when typing RESET
// We need to bind this event listener after DOM load or check if element exists
document.addEventListener('DOMContentLoaded', function () {
    const input = document.getElementById('resetConfirmationInput');
    if (input) {
        input.addEventListener('input', function (e) {
            const btn = document.getElementById('confirmResetBtn');
            if (e.target.value === 'RESET') {
                btn.disabled = false;
            } else {
                btn.disabled = true;
            }
        });
    }
});

function confirmFactoryReset() {
    const deleteFiles = document.getElementById('deleteDownloadsCheckbox').checked;
    const btn = document.getElementById('confirmResetBtn');

    // Safety check
    if (document.getElementById('resetConfirmationInput').value !== 'RESET') return;

    btn.disabled = true;
    btn.innerHTML = '<span class="btn-icon">⏳</span> Resetting...';

    fetch('/api/settings/factory-reset', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ delete_files: deleteFiles })
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showToast('Factory reset complete. Reloading...', 'success');
                setTimeout(() => {
                    window.location.reload();
                }, 1000);
            } else {
                showToast(data.error || 'Reset failed', 'error');
                btn.disabled = false;
                btn.innerHTML = 'Confirm Factory Reset';
            }
        })
        .catch(error => {
            console.error('Reset error:', error);
            showToast('Reset error: ' + error, 'error');
            btn.disabled = false;
            btn.innerHTML = 'Confirm Factory Reset';
        });
}
