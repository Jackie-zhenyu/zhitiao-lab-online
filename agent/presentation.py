"""程序填写事实数值和受控解释文案，模型没有数值或任意断言的输出通道。"""

HYPOTHESES = {
    "tracking_lag":"对象动态或控制作用可能影响跟踪响应；现有证据不能确定某个PID参数错误。",
    "noise_or_dynamics":"测量噪声、未建模动态或激励变化都可能影响记录，需要额外证据区分。",
    "actuator_limit":"输出接近已确认限值可能影响响应；这不能确认积分饱和或控制器内部状态。",
    "recording_artifact":"记录链路、时间基准或数据格式可能影响本次分析；不能据此判定设备故障。",
    "changed_conditions":"实验条件未知或不同可能影响可比性；不能直接把差异归因于PID修改。",
    "insufficient_evidence":"现有证据不足以区分原因，应先补充或核对记录。",
}
VERIFICATIONS = {
    "inspect_raw_interval":"在本地检查对应原始区间、曲线和数据质量位置。",
    "check_sampling":"核对采样周期、时间戳来源、丢样和时间重置记录。",
    "confirm_output_limits":"查阅对象与控制器资料，确认输出单位、上下限及限幅记录。",
    "repeat_same_conditions":"固定对象、负载、目标和采样设置，重复实验并保存条件记录。",
    "check_measurement_chain":"通过独立测量或校准记录检查测量链路，不直接认定传感器故障。",
    "confirm_step_baseline":"在本地确认单次阶跃区间、目标变化及响应初值的有效基线。",
    "extend_observation":"在合适的实验条件下补充观察记录，检查观测区间内的持续表现。",
}
LIMITATIONS = {
    "observation_only":"结论仅适用于本次观测；调节时间不保证未来不再离带。",
    "not_causal":"规则命中和A/B差异不构成根因诊断、PID因果结论或绝对优胜。",
    "sampling_resolution":"采样间隔与显示抽点可能遗漏变化；原始指标仍使用全部有效点。",
    "conditions_unverified":"实验条件由用户记录，未独立验证；未知或不同条件需要保留限制。",
    "no_windup_diagnosis":"没有足够控制器内部证据，不能确认积分饱和。",
    "statistical_candidate_only":"未确认合理物理变化率上限时，只能描述统计突变候选。",
}
CLARIFICATIONS = {
    "select_experiment":"请在AI区选择要分析的实验A，以及需要时的实验B。",
    "confirm_fields":"请先在本地核对并确认字段、物理量和单位。",
    "confirm_step":"请在本地确认阶跃、基线和观察区间；对比还需要保存确认快照。",
    "select_valid_interval":"请在本地选择连续有效区间，并明确一个可用的秒范围。",
    "confirm_comparison_window":"请选择两份确认快照并核对AI区的共同观察时长，重新授权本轮请求。",
    "clarify_question":"请明确实验、希望检查的现象或比较内容，避免不明确的指代。",
}
