/**
 * Smart Student Management System - Client-Side Enhancements (Phase 4)
 * - Accessible Modal Dialogs (replacing native confirm() and alert())
 * - Mobile Navigation Hamburger Menu with keyboard accessibility
 * - Double-submit protection & loading indicators
 * - Client-side real-time form validation
 * - Light / Dark mode theme switcher
 * - Anna University 10-point scale live grade preview
 */

/**
 * Escape HTML to prevent XSS in dynamic modal messages.
 */
function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

/**
 * Open styled, accessible application modal dialog.
 */
function openAppModal(options) {
    const modal = document.getElementById('app-modal');
    if (!modal) {
        if (options.onConfirm) options.onConfirm();
        return;
    }

    const titleEl = document.getElementById('modal-title');
    const msgEl = document.getElementById('modal-message');
    const confirmBtn = document.getElementById('modal-confirm-btn');
    const cancelBtn = document.getElementById('modal-cancel-btn');
    const closeBtn = document.getElementById('modal-close-btn');
    const iconContainer = document.getElementById('modal-icon');

    if (titleEl) titleEl.textContent = options.title || 'Confirm Action';
    if (msgEl) {
        if (options.isHtml) {
            msgEl.innerHTML = options.message || '';
        } else {
            msgEl.textContent = options.message || '';
        }
    }

    // Icon handling
    if (iconContainer) {
        if (options.type === 'danger') {
            iconContainer.className = 'modal-header-icon modal-icon-danger';
            iconContainer.innerHTML = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>';
        } else if (options.type === 'warning') {
            iconContainer.className = 'modal-header-icon modal-icon-warning';
            iconContainer.innerHTML = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>';
        } else {
            iconContainer.className = 'modal-header-icon modal-icon-info';
            iconContainer.innerHTML = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>';
        }
    }

    // Button states
    if (options.isAlert) {
        if (cancelBtn) cancelBtn.style.display = 'none';
        if (confirmBtn) {
            confirmBtn.textContent = options.confirmText || 'OK';
            confirmBtn.className = 'btn btn-primary';
        }
    } else {
        if (cancelBtn) {
            cancelBtn.style.display = '';
            cancelBtn.textContent = options.cancelText || 'Cancel';
        }
        if (confirmBtn) {
            confirmBtn.textContent = options.confirmText || 'Confirm';
            confirmBtn.className = 'btn ' + (options.confirmClass || 'btn-danger');
        }
    }

    modal.hidden = false;
    modal.classList.add('is-open');
    document.body.classList.add('modal-open');
    if (confirmBtn) confirmBtn.focus();

    const cleanup = () => {
        modal.hidden = true;
        modal.classList.remove('is-open');
        document.body.classList.remove('modal-open');
        if (confirmBtn) confirmBtn.onclick = null;
        if (cancelBtn) cancelBtn.onclick = null;
        if (closeBtn) closeBtn.onclick = null;
        document.removeEventListener('keydown', handleKey);
        modal.removeEventListener('click', handleBackdrop);
    };

    const handleKey = (e) => {
        if (e.key === 'Escape') {
            e.preventDefault();
            cleanup();
            if (options.onCancel) options.onCancel();
        }
    };

    const handleBackdrop = (e) => {
        if (e.target === modal) {
            cleanup();
            if (options.onCancel) options.onCancel();
        }
    };

    document.addEventListener('keydown', handleKey);
    modal.addEventListener('click', handleBackdrop);

    if (confirmBtn) {
        confirmBtn.onclick = () => {
            cleanup();
            if (options.onConfirm) options.onConfirm();
        };
    }

    if (cancelBtn) {
        cancelBtn.onclick = () => {
            cleanup();
            if (options.onCancel) options.onCancel();
        };
    }

    if (closeBtn) {
        closeBtn.onclick = () => {
            cleanup();
            if (options.onCancel) options.onCancel();
        };
    }
}

/**
 * Replace browser alert() with styled modal.
 */
function showAppAlert(message, title, type) {
    openAppModal({
        title: title || 'Notice',
        message: message,
        isAlert: true,
        type: type || 'info'
    });
}

/**
 * Confirm student record deletion.
 * Retains exact function signature: confirmStudentDelete(studentName, registerNumber)
 * Compatible with inline onsubmit="return confirmStudentDelete(...)" handlers.
 */
function confirmStudentDelete(studentName, registerNumber, evt) {
    const e = evt || window.event;
    const form = e && e.target ? (e.target.tagName === 'FORM' ? e.target : e.target.closest('form')) : document.activeElement ? document.activeElement.closest('form') : null;

    if (form && form.dataset.confirmed === 'true') {
        return true;
    }

    if (e && typeof e.preventDefault === 'function') {
        e.preventDefault();
    }

    openAppModal({
        title: 'Delete Student Record',
        message: 'Are you sure you want to permanently delete the student record for <strong>' +
            escapeHtml(studentName) + '</strong> (' + escapeHtml(registerNumber) + ')?<br><br><span style="color: var(--danger); font-size: 13px; font-weight: 500;">&bull; This action cannot be undone.</span>',
        isHtml: true,
        type: 'danger',
        confirmText: 'Delete Record',
        confirmClass: 'btn-danger',
        onConfirm: function () {
            if (form) {
                form.dataset.confirmed = 'true';
                form.submit();
            }
        }
    });

    return false;
}

document.addEventListener('DOMContentLoaded', function () {
    // 1. Alert dismissal
    document.querySelectorAll('.alert-close').forEach(button => {
        button.addEventListener('click', function () {
            const alertBox = this.closest('.alert');
            if (alertBox) {
                alertBox.style.opacity = '0';
                alertBox.style.transform = 'translateY(-4px)';
                setTimeout(() => alertBox.remove(), 180);
            }
        });
    });

    // 2. Mobile Menu Hamburger Toggle (Phase 4)
    const navToggle = document.getElementById('nav-toggle');
    const mobileNav = document.getElementById('mobile-nav');

    if (navToggle && mobileNav) {
        const toggleMenu = (open) => {
            const isOpen = open !== undefined ? open : navToggle.getAttribute('aria-expanded') !== 'true';
            navToggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
            navToggle.classList.toggle('active', isOpen);
            mobileNav.hidden = !isOpen;
            mobileNav.classList.toggle('is-open', isOpen);
            document.body.classList.toggle('nav-open', isOpen);
        };

        navToggle.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleMenu();
        });

        // Close on Escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && navToggle.getAttribute('aria-expanded') === 'true') {
                toggleMenu(false);
                navToggle.focus();
            }
        });

        // Close when clicking outside
        document.addEventListener('click', (e) => {
            if (navToggle.getAttribute('aria-expanded') === 'true' &&
                !mobileNav.contains(e.target) &&
                !navToggle.contains(e.target)) {
                toggleMenu(false);
            }
        });
    }

    // 3. Intercept forms with legacy inline onsubmit confirm(...) and upgrade to styled modal
    document.querySelectorAll('form').forEach(form => {
        const onsubmitAttr = form.getAttribute('onsubmit');
        if (onsubmitAttr && onsubmitAttr.includes('confirm(') && !onsubmitAttr.includes('confirmStudentDelete(')) {
            // Extract the confirmation message from inside confirm('...')
            const match = onsubmitAttr.match(/confirm\((['"])(.*?)\1\)/s);
            const promptMsg = match ? match[2].replace(/\\n/g, '<br>').replace(/\\'/g, "'") : 'Are you sure you want to proceed?';
            
            // Remove inline attribute to handle via styled modal listener
            form.removeAttribute('onsubmit');
            form.addEventListener('submit', function (e) {
                if (form.dataset.confirmed === 'true') return;
                e.preventDefault();

                openAppModal({
                    title: 'Please Confirm',
                    message: promptMsg,
                    isHtml: true,
                    type: promptMsg.toLowerCase().includes('delete') || promptMsg.toLowerCase().includes('anonymize') ? 'danger' : 'info',
                    confirmText: 'Proceed',
                    confirmClass: promptMsg.toLowerCase().includes('delete') || promptMsg.toLowerCase().includes('anonymize') ? 'btn-danger' : 'btn-primary',
                    onConfirm: function () {
                        form.dataset.confirmed = 'true';
                        form.submit();
                    }
                });
            });
        }
    });

    // 4. Live grade preview (Anna University 10-point scale)
    const marksInput = document.getElementById('marks');
    const gradePreview = document.getElementById('grade-preview');

    if (marksInput && gradePreview) {
        const SCALE = [
            { min: 90, cls: 'badge-grade-o',     label: 'Grade O \u2022 10.0 (Outstanding)' },
            { min: 80, cls: 'badge-grade-aplus', label: 'Grade A+ \u2022 9.0 (Excellent)' },
            { min: 70, cls: 'badge-grade-a',     label: 'Grade A \u2022 8.0 (Very Good)' },
            { min: 60, cls: 'badge-grade-bplus', label: 'Grade B+ \u2022 7.0 (Good)' },
            { min: 50, cls: 'badge-grade-b',     label: 'Grade B \u2022 6.0 (Fair / Pass)' },
            { min: 0,  cls: 'badge-grade-ra',    label: 'Grade RA \u2022 0.0 (Reappear)' }
        ];

        const render = (cls, text) => {
            const badge = document.createElement('span');
            badge.className = 'badge ' + cls;
            badge.textContent = text;
            gradePreview.replaceChildren(badge);
        };

        const updateGradePreview = () => {
            const val = parseFloat(marksInput.value);
            if (isNaN(val)) return render('badge-neutral', 'Enter score');
            if (val < 0 || val > 100) return render('badge-grade-ra', 'Invalid (0-100)');
            const match = SCALE.find(s => val >= s.min);
            render(match.cls, match.label);
        };

        marksInput.addEventListener('input', updateGradePreview);
        updateGradePreview();
    }

    // 5. Double-submit prevention & Loading States (Phase 4)
    document.querySelectorAll('form').forEach(form => {
        form.addEventListener('submit', function (e) {
            if (e.defaultPrevented) return;
            const submitBtn = form.querySelector('button[type="submit"]');
            if (!submitBtn || submitBtn.disabled || !form.checkValidity()) return;

            const originalHTML = submitBtn.innerHTML;
            setTimeout(() => {
                submitBtn.disabled = true;
                submitBtn.classList.add('is-loading');
                submitBtn.innerHTML =
                    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="spinner-icon" aria-hidden="true">' +
                    '<circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg>' +
                    '<span>Processing\u2026</span>';
            }, 0);

            window.addEventListener('pageshow', function restore() {
                submitBtn.disabled = false;
                submitBtn.classList.remove('is-loading');
                submitBtn.innerHTML = originalHTML;
                window.removeEventListener('pageshow', restore);
            });
        });
    });

    // 6. Photo upload validation with styled modal (Max 3MB, images only)
    const photoInput = document.getElementById('photo');
    if (photoInput) {
        photoInput.addEventListener('change', function () {
            const file = this.files && this.files[0];
            if (!file) return;
            if (file.type && !file.type.startsWith('image/')) {
                showAppAlert('Please choose a valid image file format (PNG, JPG, JPEG, or WebP).', 'Invalid File Type', 'warning');
                this.value = '';
                return;
            }
            if (file.size / 1024 / 1024 > 3.0) {
                showAppAlert('The selected image is larger than 3MB. Please choose a smaller file.', 'File Size Exceeded', 'warning');
                this.value = '';
            }
        });
    }

    // 7. Light / Dark theme switcher
    const themeToggleBtns = document.querySelectorAll('#theme-toggle');
    const SUN = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="5"></circle><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"></path></svg>';
    const MOON = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>';

    const updateThemeButtons = theme => {
        themeToggleBtns.forEach(btn => {
            const dark = theme === 'dark';
            btn.innerHTML = (dark ? SUN : MOON) + '<span class="theme-text">' + (dark ? 'Light' : 'Dark') + '</span>';
            btn.setAttribute('title', dark ? 'Switch to Light Mode' : 'Switch to Dark Mode');
            btn.setAttribute('aria-label', dark ? 'Switch to Light Mode' : 'Switch to Dark Mode');
        });
    };

    updateThemeButtons(document.documentElement.getAttribute('data-theme') || 'light');

    themeToggleBtns.forEach(btn => {
        btn.addEventListener('click', function () {
            const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', next);
            try { localStorage.setItem('theme', next); } catch (e) {}
            updateThemeButtons(next);
        });
    });

    // 8. Real-time client-side field validation feedback
    const activeForm = document.querySelector('form#student-form, form#edit-student-form');
    if (activeForm) {
        const inputs = activeForm.querySelectorAll('input[required], select[required]');
        inputs.forEach(input => {
            input.addEventListener('blur', function () {
                const group = input.closest('.form-group');
                if (!group) return;
                let existingErr = group.querySelector('.field-error-message');

                if (!input.checkValidity()) {
                    group.classList.add('has-error');
                    input.setAttribute('aria-invalid', 'true');
                    if (!existingErr) {
                        const errDiv = document.createElement('div');
                        errDiv.className = 'field-error-message';
                        errDiv.setAttribute('role', 'alert');
                        errDiv.id = input.id + '-error';
                        errDiv.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg> <span>' + (input.validationMessage || 'This field is required.') + '</span>';
                        input.parentNode.insertBefore(errDiv, input.nextSibling);
                        input.setAttribute('aria-describedby', errDiv.id);
                    } else {
                        existingErr.querySelector('span').textContent = input.validationMessage || 'This field is required.';
                    }
                } else {
                    group.classList.remove('has-error');
                    input.removeAttribute('aria-invalid');
                    if (existingErr) existingErr.remove();
                }
            });

            input.addEventListener('input', function () {
                if (input.checkValidity()) {
                    const group = input.closest('.form-group');
                    if (group) group.classList.remove('has-error');
                    input.removeAttribute('aria-invalid');
                    const err = group ? group.querySelector('.field-error-message') : null;
                    if (err) err.remove();
                }
            });
        });
    }

    // 9. Live Dashboard Polling (30-second interval - Phase 6)
    const kpiTotalEl = document.getElementById('kpi-total-students');
    if (kpiTotalEl) {
        const liveBadge = document.getElementById('live-indicator');

        const pollDashboardStats = async () => {
            try {
                const response = await fetch('/api/v1/dashboard/kpis', {
                    headers: { 'Accept': 'application/json' },
                    credentials: 'same-origin'
                });
                if (!response.ok) return;

                const data = await response.json();
                if (data && data.success && data.kpis) {
                    const k = data.kpis;
                    if (kpiTotalEl) kpiTotalEl.textContent = k.total_students;

                    const avgEl = document.getElementById('kpi-avg-marks');
                    if (avgEl) avgEl.innerHTML = `${k.avg_marks} <span style="font-size: 16px; font-weight: normal; color: var(--text-muted);">/ 100</span>`;

                    const maxEl = document.getElementById('kpi-max-marks');
                    if (maxEl) maxEl.innerHTML = `${k.max_marks} <span style="font-size: 16px; font-weight: normal; color: var(--text-muted);">/ 100</span>`;

                    const passEl = document.getElementById('kpi-pass-percentage');
                    if (passEl) passEl.textContent = `${k.pass_percentage}%`;

                    const passSubEl = document.getElementById('kpi-pass-subtext');
                    if (passSubEl) passSubEl.textContent = `${k.pass_count} Passed • ${k.fail_count} At Risk (< 50%)`;

                    // Update Chart.js instances if registered globally
                    if (window.deptChart && data.chart_dept_data) {
                        window.deptChart.data.datasets[0].data = data.chart_dept_data;
                        window.deptChart.update('none');
                    }
                    if (window.gradeChart && data.chart_grade_data) {
                        window.gradeChart.data.datasets[0].data = data.chart_grade_data;
                        window.gradeChart.update('none');
                    }

                    if (liveBadge) {
                        liveBadge.classList.add('live-pulse');
                        setTimeout(() => liveBadge.classList.remove('live-pulse'), 1000);
                    }
                }
            } catch (err) {
                console.debug('Dashboard polling skipped:', err);
            }
        };

        // Poll every 30 seconds
        setInterval(pollDashboardStats, 30000);
    }
});
