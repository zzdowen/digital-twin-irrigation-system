using UnityEngine;

/// <summary>
/// 场景观察相机：绕目标点旋转 / 平移 / 缩放。
/// 操作：左键拖拽=旋转，右键拖拽=平移，滚轮=缩放（与场景设计文档一致）。
/// </summary>
public class OrbitCamera : MonoBehaviour
{
    [Header("观察目标")]
    [SerializeField] private Vector3 target = new Vector3(0f, 0.7f, 0f);

    [Header("距离限制")]
    [SerializeField] private float distance = 7.5f;
    [SerializeField] private float minDistance = 3.5f;
    [SerializeField] private float maxDistance = 14f;

    [Header("旋转灵敏度")]
    [SerializeField] private float rotateSpeed = 4f;
    [SerializeField] private float panSpeed = 0.02f;

    [Header("俯仰角限制")]
    [SerializeField] private float minPitch = 12f;   // 最低仰角（避免穿地）
    [SerializeField] private float maxPitch = 80f;   // 最高俯角

    private float _yaw;
    private float _pitch = 42f;

    private void Start()
    {
        // 由当前相机位置推导初始朝向
        Vector3 offset = transform.position - target;
        distance = Mathf.Clamp(offset.magnitude, minDistance, maxDistance);
        _yaw = Mathf.Atan2(offset.x, offset.z) * Mathf.Rad2Deg;
        _pitch = Mathf.Asin(offset.y / offset.magnitude) * Mathf.Rad2Deg;
        ApplyTransform();
    }

    private void Update()
    {
        HandleRotation();
        HandlePan();
        HandleZoom();
        ApplyTransform();
    }

    private void HandleRotation()
    {
        if (!Input.GetMouseButton(0)) return;
        _yaw += Input.GetAxis("Mouse X") * rotateSpeed;
        _pitch -= Input.GetAxis("Mouse Y") * rotateSpeed;
        _pitch = Mathf.Clamp(_pitch, minPitch, maxPitch);
    }

    private void HandlePan()
    {
        if (!Input.GetMouseButton(1)) return;
        float dx = -Input.GetAxis("Mouse X") * panSpeed * distance;
        float dy = -Input.GetAxis("Mouse Y") * panSpeed * distance;

        // 沿相机平面平移目标点
        Vector3 right = transform.right;
        Vector3 up = Vector3.Cross(right, Vector3.up).normalized;
        // 简化为水平平移 + 竖直平移
        target += right * dx;
        target += Vector3.up * dy;
    }

    private void HandleZoom()
    {
        float scroll = Input.GetAxis("Mouse ScrollWheel");
        if (Mathf.Abs(scroll) < 0.0001f) return;
        distance = Mathf.Clamp(distance - scroll * distance * 0.9f, minDistance, maxDistance);
    }

    private void ApplyTransform()
    {
        Quaternion rot = Quaternion.Euler(_pitch, _yaw, 0f);
        transform.position = target - rot * Vector3.forward * distance;
        transform.rotation = rot;
    }
}
