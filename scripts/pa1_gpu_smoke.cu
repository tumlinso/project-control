#include <cuda_runtime.h>

#include <cstdint>
#include <iostream>
#include <vector>

namespace {

constexpr int kItems = 4096;

__global__ void transform(const std::uint32_t* input, std::uint32_t* output, int count) {
  const int index = blockIdx.x * blockDim.x + threadIdx.x;
  if (index < count) output[index] = input[index] * 3U + 1U;
}

bool check_device(int ordinal, std::uint64_t* sum_out) {
  if (cudaSetDevice(ordinal) != cudaSuccess) return false;

  std::vector<std::uint32_t> input(kItems);
  std::vector<std::uint32_t> output(kItems, 0U);
  for (int i = 0; i < kItems; ++i) input[i] = static_cast<std::uint32_t>(i);

  std::uint32_t* device_input = nullptr;
  std::uint32_t* device_output = nullptr;
  if (cudaMalloc(reinterpret_cast<void**>(&device_input), kItems * sizeof(std::uint32_t)) != cudaSuccess) return false;
  if (cudaMalloc(reinterpret_cast<void**>(&device_output), kItems * sizeof(std::uint32_t)) != cudaSuccess) {
    cudaFree(device_input);
    return false;
  }

  bool valid = cudaMemcpy(device_input, input.data(), kItems * sizeof(std::uint32_t),
                          cudaMemcpyHostToDevice) == cudaSuccess;
  if (valid) {
    transform<<<(kItems + 255) / 256, 256>>>(device_input, device_output, kItems);
    valid = cudaGetLastError() == cudaSuccess;
  }
  if (valid) {
    valid = cudaMemcpy(output.data(), device_output, kItems * sizeof(std::uint32_t),
                       cudaMemcpyDeviceToHost) == cudaSuccess;
  }

  std::uint64_t sum = 0;
  if (valid) {
    for (int i = 0; i < kItems; ++i) {
      const std::uint32_t expected = static_cast<std::uint32_t>(i) * 3U + 1U;
      if (output[i] != expected) {
        valid = false;
        break;
      }
      sum += output[i];
    }
  }

  const bool free_output = cudaFree(device_output) == cudaSuccess;
  const bool free_input = cudaFree(device_input) == cudaSuccess;
  *sum_out = sum;
  return valid && free_output && free_input;
}

}  // namespace

int main() {
  int count = 0;
  if (cudaGetDeviceCount(&count) != cudaSuccess || count != 4) {
    std::cerr << "expected exactly four controller-visible devices\n";
    return 2;
  }

  std::cout << "{\"format\":\"pa1-gpu-smoke/1\",\"status\":\"ok\",\"devices\":[";
  for (int ordinal = 0; ordinal < count; ++ordinal) {
    std::uint64_t sum = 0;
    if (!check_device(ordinal, &sum)) {
      std::cerr << "GPU correctness or cleanup failed on visible ordinal " << ordinal << "\n";
      return 3;
    }
    if (ordinal != 0) std::cout << ",";
    std::cout << "{\"visible_ordinal\":" << ordinal
              << ",\"items\":" << kItems << ",\"sum\":" << sum
              << ",\"passed\":true}";
  }
  std::cout << "]}\n";
  return 0;
}
