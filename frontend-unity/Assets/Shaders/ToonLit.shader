// 数字孪生灌溉系统 - 动漫风格卡通着色器
// 特性：阶梯光照（toon ramp）+ 阴影色调 + 描边（inverted hull）
// 适用：内置渲染管线（Built-in RP），所有程序化模型统一使用，画面简洁低饱和。
Shader "Custom/ToonLit"
{
    Properties
    {
        _Color ("主色", Color) = (1,1,1,1)
        _RampSteps ("光照阶梯数", Range(2, 8)) = 3
        _ShadowTint ("阴影色调", Color) = (0.55, 0.62, 0.78, 1)
        _Brightness ("基础亮度", Range(0, 1)) = 0.5
        _OutlineColor ("描边颜色", Color) = (0.16, 0.18, 0.22, 1)
        _OutlineWidth ("描边宽度", Range(0, 0.05)) = 0.012
    }
    SubShader
    {
        Tags { "RenderType" = "Opaque" }
        LOD 200

        // ---- Pass 1: 描边（背面外扩 inverted hull）----
        Pass
        {
            Name "Outline"
            Cull Front
            ZWrite On

            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"

            float _OutlineWidth;
            fixed4 _OutlineColor;

            struct appdata
            {
                float4 vertex : POSITION;
                float3 normal : NORMAL;
            };

            struct v2f
            {
                float4 pos : SV_POSITION;
            };

            v2f vert(appdata v)
            {
                v2f o;
                float3 n = normalize(v.normal);
                v.vertex.xyz += n * _OutlineWidth;
                o.pos = UnityObjectToClipPos(v.vertex);
                return o;
            }

            fixed4 frag(v2f i) : SV_Target
            {
                return _OutlineColor;
            }
            ENDCG
        }

        // ---- Pass 2: 主光照（toon ramp 阶梯漫反射）----
        Pass
        {
            Name "Main"
            Cull Back
            ZWrite On

            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_fwdbase
            #include "UnityCG.cginc"
            #include "Lighting.cginc"
            #include "AutoLight.cginc"

            fixed4 _Color;
            float _RampSteps;
            fixed4 _ShadowTint;
            float _Brightness;

            struct appdata
            {
                float4 vertex : POSITION;
                float3 normal : NORMAL;
            };

            struct v2f
            {
                float4 pos : SV_POSITION;
                float3 worldNormal : TEXCOORD0;
                float3 worldPos : TEXCOORD1;
                SHADOW_COORDS(2)
            };

            v2f vert(appdata v)
            {
                v2f o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.worldNormal = UnityObjectToWorldNormal(v.normal);
                o.worldPos = mul(unity_ObjectToWorld, v.vertex).xyz;
                TRANSFER_SHADOW(o);
                return o;
            }

            fixed4 frag(v2f i) : SV_Target
            {
                float3 n = normalize(i.worldNormal);
                float3 lightDir = normalize(_WorldSpaceLightPos0.xyz);
                float NdotL = dot(n, lightDir) * 0.5 + 0.5;

                // 阶梯化
                float ramp = floor(NdotL * _RampSteps) / _RampSteps;
                ramp = _Brightness + ramp * (1.0 - _Brightness);

                // 亮部用主色，暗部混入阴影色调
                fixed3 lit = lerp(_ShadowTint.rgb, _Color.rgb, ramp);

                // 阴影
                float shadow = SHADOW_ATTENUATION(i);
                lit *= (0.35 + 0.65 * shadow);

                // 环境光
                lit += UNITY_LIGHTMODEL_AMBIENT.rgb * 0.25;

                return fixed4(lit, _Color.a);
            }
            ENDCG
        }

        // 阴影投射（让模型在 toon 下也能投射接收阴影）
        UsePass "Legacy Shaders/VertexLit/SHADOWCASTER"
    }
    FallBack "Diffuse"
}
