/**
 * Glint 3-Stage Customizer Engine
 * Handles real-time recalculation, wizard navigation, live preview, and draft saving.
 */

class GlintCustomizer {
    constructor(boxData, itemsCatalog, prefillData) {
        this.box = boxData;
        this.catalog = itemsCatalog; // array of items with {id, name, price, emoji, category, etc}
        this.selectedItems = {}; // { itemId: quantity }
        this.currentStep = 1;

        // Initialize with prefilled items or defaults
        if (prefillData && prefillData.selected_item_map) {
            this.selectedItems = { ...prefillData.selected_item_map };
        }

        this.initElements();
        this.initEvents();
        this.updateState();
    }

    initElements() {
        this.step1Pane = document.getElementById('step-1-pane');
        this.step2Pane = document.getElementById('step-2-pane');
        this.step3Pane = document.getElementById('step-3-pane');

        this.node1 = document.getElementById('node-1');
        this.node2 = document.getElementById('node-2');
        this.node3 = document.getElementById('node-3');
        this.progressBar = document.getElementById('wizard-progress-bar');

        this.runningPriceEl = document.getElementById('running-total-price');
        this.reviewSubtotalEl = document.getElementById('review-subtotal-price');
        this.reviewTaxEl = document.getElementById('review-tax-price');
        this.reviewGrandTotalEl = document.getElementById('review-grand-total-price');
        this.previewItemsContainer = document.getElementById('box-preview-items');
        this.selectedItemsJsonInput = document.getElementById('selected_items_json');

        this.recipientNameInput = document.getElementById('personalization_name');
        this.messageInput = document.getElementById('personalization_message');
        this.instructionsInput = document.getElementById('personalization_instructions');
    }

    initEvents() {
        // Step Navigation Buttons
        const nextToStep2Btn = document.getElementById('btn-to-step-2');
        if (nextToStep2Btn) {
            nextToStep2Btn.addEventListener('click', () => this.goToStep(2));
        }

        const backToStep1Btn = document.getElementById('btn-back-to-step-1');
        if (backToStep1Btn) {
            backToStep1Btn.addEventListener('click', () => this.goToStep(1));
        }

        const nextToStep3Btn = document.getElementById('btn-to-step-3');
        if (nextToStep3Btn) {
            nextToStep3Btn.addEventListener('click', () => {
                if (!this.validateStep2()) return;
                this.goToStep(3);
            });
        }

        const backToStep2Btn = document.getElementById('btn-back-to-step-2');
        if (backToStep2Btn) {
            backToStep2Btn.addEventListener('click', () => this.goToStep(2));
        }

        // Save Draft Buttons
        const saveDraftBtns = document.querySelectorAll('.btn-save-draft');
        saveDraftBtns.forEach(btn => {
            btn.addEventListener('click', () => this.saveDraft());
        });

        // Checkboxes and Quantity Controls
        document.querySelectorAll('.item-checkbox').forEach(cb => {
            cb.addEventListener('change', (e) => {
                const itemId = parseInt(e.target.dataset.itemId);
                if (e.target.checked) {
                    this.selectedItems[itemId] = this.selectedItems[itemId] || 1;
                } else {
                    delete this.selectedItems[itemId];
                }
                this.updateState();
            });
        });

        document.querySelectorAll('.qty-btn-plus').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const itemId = parseInt(btn.dataset.itemId);
                const currentQty = this.selectedItems[itemId] || 0;
                this.selectedItems[itemId] = currentQty + 1;

                const cb = document.getElementById(`cb-item-${itemId}`);
                if (cb) cb.checked = true;

                this.updateState();
            });
        });

        document.querySelectorAll('.qty-btn-minus').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const itemId = parseInt(btn.dataset.itemId);
                const currentQty = this.selectedItems[itemId] || 0;
                if (currentQty > 1) {
                    this.selectedItems[itemId] = currentQty - 1;
                } else if (currentQty === 1) {
                    delete this.selectedItems[itemId];
                    const cb = document.getElementById(`cb-item-${itemId}`);
                    if (cb) cb.checked = false;
                }
                this.updateState();
            });
        });
    }

    goToStep(stepNumber) {
        if (stepNumber === 2 && Object.keys(this.selectedItems).length === 0) {
            alert('Please select at least one item for your gift box.');
            return;
        }

        this.currentStep = stepNumber;

        // Update Panes
        if (this.step1Pane) this.step1Pane.style.display = (stepNumber === 1) ? 'block' : 'none';
        if (this.step2Pane) this.step2Pane.style.display = (stepNumber === 2) ? 'block' : 'none';
        if (this.step3Pane) this.step3Pane.style.display = (stepNumber === 3) ? 'block' : 'none';

        // Update Progress Stepper Nodes
        [this.node1, this.node2, this.node3].forEach((node, idx) => {
            const step = idx + 1;
            if (node) {
                node.classList.remove('active', 'completed');
                if (step === stepNumber) {
                    node.classList.add('active');
                } else if (step < stepNumber) {
                    node.classList.add('completed');
                }
            }
        });

        if (this.progressBar) {
            if (stepNumber === 1) this.progressBar.style.width = '0%';
            if (stepNumber === 2) this.progressBar.style.width = '50%';
            if (stepNumber === 3) this.progressBar.style.width = '100%';
        }

        if (stepNumber === 3) {
            this.renderReviewSummary();
        }

        window.scrollTo({ top: 120, behavior: 'smooth' });
    }

    validateStep2() {
        const name = this.recipientNameInput ? this.recipientNameInput.value.trim() : '';
        if (!name) {
            if (this.recipientNameInput) {
                this.recipientNameInput.focus();
                this.recipientNameInput.classList.add('is-invalid');
            }
            alert("Please enter the recipient's name before proceeding.");
            return false;
        }
        if (this.recipientNameInput) this.recipientNameInput.classList.remove('is-invalid');
        return true;
    }

    calculateTotal() {
        let total = parseFloat(this.box.base_price);
        const selectedList = [];

        for (const [idStr, qty] of Object.entries(this.selectedItems)) {
            const itemId = parseInt(idStr);
            const item = this.catalog.find(i => i.id === itemId);
            if (item && qty > 0) {
                total += item.price * qty;
                selectedList.push({
                    id: item.id,
                    name: item.name,
                    category: item.category,
                    price: item.price,
                    emoji: item.emoji,
                    qty: qty,
                });
            }
        }

        return { total, selectedList };
    }

    updateState() {
        const { total, selectedList } = this.calculateTotal();

        // Update Displayed Price
        if (this.runningPriceEl) {
            this.runningPriceEl.textContent = `₹${total.toFixed(2)}`;
        }

        // Update Item Card styles & quantity badges
        this.catalog.forEach(item => {
            const card = document.getElementById(`item-card-${item.id}`);
            const qtyValEl = document.getElementById(`qty-val-${item.id}`);
            const cb = document.getElementById(`cb-item-${item.id}`);
            const qty = this.selectedItems[item.id] || 0;

            if (qty > 0) {
                if (card) card.classList.add('selected');
                if (cb) cb.checked = true;
                if (qtyValEl) qtyValEl.textContent = qty;
            } else {
                if (card) card.classList.remove('selected');
                if (cb) cb.checked = false;
                if (qtyValEl) qtyValEl.textContent = '0';
            }
        });

        // Update Visualizer Box Preview
        if (this.previewItemsContainer) {
            if (selectedList.length === 0) {
                this.previewItemsContainer.innerHTML = '<p class="text-muted small w-100 text-center my-auto">Select items from the left to preview your box contents!</p>';
            } else {
                this.previewItemsContainer.innerHTML = selectedList.map(item => `
                    <span class="preview-pill">
                        ${item.emoji} ${item.name} ${item.qty > 1 ? `<b>(${item.qty}x)</b>` : ''}
                    </span>
                `).join('');
            }
        }

        // Update Hidden Input for Form Submission
        if (this.selectedItemsJsonInput) {
            this.selectedItemsJsonInput.value = JSON.stringify(selectedList);
        }
    }

    renderReviewSummary() {
        const { total, selectedList } = this.calculateTotal();
        const reviewItemsListEl = document.getElementById('review-items-list');
        const reviewRecipientEl = document.getElementById('review-recipient-name');
        const reviewMessageEl = document.getElementById('review-message-text');
        const reviewInstructionsEl = document.getElementById('review-instructions-text');

        if (reviewRecipientEl) {
            reviewRecipientEl.textContent = this.recipientNameInput.value.trim() || 'Not specified';
        }
        if (reviewMessageEl) {
            reviewMessageEl.textContent = this.messageInput.value.trim() || '(No custom message provided)';
        }
        if (reviewInstructionsEl) {
            const instrVal = this.instructionsInput ? this.instructionsInput.value.trim() : '';
            reviewInstructionsEl.textContent = instrVal || '(None)';
        }

        if (reviewItemsListEl) {
            reviewItemsListEl.innerHTML = `
                <div class="d-flex justify-content-between py-2 border-bottom">
                    <span class="fw-bold">📦 Base Gift Box (${this.box.name})</span>
                    <span class="fw-bold text-primary">₹${parseFloat(this.box.base_price).toFixed(2)}</span>
                </div>
                ${selectedList.map(it => `
                    <div class="d-flex justify-content-between py-2 border-bottom">
                        <span>${it.emoji} ${it.name} <small class="text-muted">(${it.qty}x @ ₹${it.price.toFixed(2)})</small></span>
                        <span class="fw-bold text-dark">₹${(it.price * it.qty).toFixed(2)}</span>
                    </div>
                `).join('')}
            `;
        }

        const tax = total * 0.05;
        const grandTotal = total + tax;

        if (this.reviewSubtotalEl) this.reviewSubtotalEl.textContent = `₹${total.toFixed(2)}`;
        if (this.reviewTaxEl) this.reviewTaxEl.textContent = `₹${tax.toFixed(2)}`;
        if (this.reviewGrandTotalEl) this.reviewGrandTotalEl.textContent = `₹${grandTotal.toFixed(2)}`;
    }

    async saveDraft() {
        const { total, selectedList } = this.calculateTotal();
        const draftName = prompt('Enter a name for this saved draft:', `Custom ${this.box.name}`);
        if (!draftName) return;

        const payload = {
            box_id: this.box.id,
            draft_id: this.box.draft_id || null,
            draft_name: draftName,
            items: selectedList,
            personalization_name: this.recipientNameInput ? this.recipientNameInput.value.trim() : '',
            personalization_message: this.messageInput ? this.messageInput.value.trim() : '',
            special_instructions: this.instructionsInput ? this.instructionsInput.value.trim() : '',
            total_price: total,
        };

        try {
            const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
            const res = await fetch('/api/save-draft/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken,
                },
                body: JSON.stringify(payload),
            });

            const data = await res.json();
            if (data.status === 'success') {
                this.box.draft_id = data.draft_id;
                alert(`🎉 ${data.message}`);
                if (data.is_guest) {
                    if (confirm('Tip: Register or log in to keep your drafts saved permanently across all devices! Would you like to log in now?')) {
                        window.location.href = '/login/?next=' + encodeURIComponent(window.location.pathname);
                    }
                }
            } else {
                alert(`Error saving draft: ${data.message}`);
            }
        } catch (err) {
            alert('Failed to save draft. Please check your internet connection.');
        }
    }
}
