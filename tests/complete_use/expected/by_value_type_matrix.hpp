#ifndef TESTS_COMPLETE_USE_BY_VALUE_TYPE_MATRIX_HPP_
#define TESTS_COMPLETE_USE_BY_VALUE_TYPE_MATRIX_HPP_

#include "value_shapes_provider.hpp"

class MatrixAdjustedFunctionReturn;
class MatrixArrayParameter;
class MatrixLvalueParameter;
class MatrixLvalueReturn;
class MatrixMemberOwner;
class MatrixMemberPointee;
class MatrixNestedValueArgument;
class MatrixPointerParameter;
class MatrixPointerReturn;
class MatrixRvalueParameter;
class MatrixRvalueReturn;

void MatrixTakeByValue(MatrixByValueParameter value);
MatrixByValueReturn MatrixReturnByValue();

void MatrixTakeRvalue(MatrixRvalueParameter&& value);
MatrixRvalueReturn&& MatrixReturnRvalue();
void MatrixTakeMemberPointer(MatrixMemberPointee MatrixMemberOwner::* value);
MatrixMemberPointee MatrixMemberOwner::* MatrixReturnMemberPointer();
void MatrixTakeAdjustedFunction(MatrixAdjustedFunctionReturn callback());
void MatrixTakeArray(MatrixArrayParameter values[3]);
void MatrixTakeLvalue(MatrixLvalueParameter& value);
MatrixLvalueReturn& MatrixReturnLvalue();
void MatrixTakePointer(MatrixPointerParameter* value);
MatrixPointerReturn* MatrixReturnPointer();
MatrixByValueBox<MatrixNestedValueArgument> MatrixReturnNestedByValue();

#endif  // TESTS_COMPLETE_USE_BY_VALUE_TYPE_MATRIX_HPP_
