// Complete list_ocr_results envelope for synthetic component fixtures.
module.exports = function candidates(order_id, results = [], workflow = {}) {
  return { order_id, results, selected_ocr_result_id: workflow.selected_ocr_result_id ?? null,
    workflow_state: workflow.state ?? 'not_initialized', blockers: workflow.blockers ?? ['workflow_not_initialized'],
    candidate_template_version_id: workflow.template_version_id ?? null,
    hidden_template_mismatch_result_count: 0 };
};
